from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool
from pydantic import BaseModel, Field

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue
from app.tool.models import ActionResult, ToolSourceType, ToolSpec
from app.tool.registry import ToolRegistry


class MCPTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerConfig(BaseModel):
    server_id: str
    transport: MCPTransport = MCPTransport.STDIO
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30.0


@dataclass
class _ActiveMCPConnection:
    config: MCPServerConfig
    session: ClientSession
    exit_stack: AsyncExitStack
    tools: dict[str, ToolSpec]


class MCPProvider:
    def __init__(self, servers: list[MCPServerConfig] | None = None) -> None:
        self.servers = list(servers or [])
        self._connections: dict[str, _ActiveMCPConnection] = {}

    async def register_tools(self, registry: ToolRegistry) -> ToolRegistry:
        for server in self.servers:
            connection = await self._connect_server(server)
            for spec in connection.tools.values():
                registry.register(spec, self._build_executor(server.server_id, spec.name))
        return registry

    async def close(self) -> None:
        for connection in self._connections.values():
            await connection.exit_stack.aclose()
        self._connections.clear()

    def configured_server_count(self) -> int:
        return len(self.servers)

    def connected_server_count(self) -> int:
        return len(self._connections)

    def registered_tool_count(self) -> int:
        return sum(len(status["registered_tools"]) for status in self.server_status())

    def server_status(self) -> list[dict[str, JsonValue]]:
        statuses: list[dict[str, JsonValue]] = []
        for server in self.servers:
            connection = self._connections.get(server.server_id)
            registered_tools = sorted(connection.tools) if connection is not None else []
            statuses.append(
                {
                    "server_id": server.server_id,
                    "transport": server.transport.value,
                    "connected": connection is not None,
                    "registered_tools": registered_tools,
                    "tool_count": len(registered_tools),
                }
            )
        return statuses

    async def _connect_server(self, config: MCPServerConfig) -> _ActiveMCPConnection:
        existing = self._connections.get(config.server_id)
        if existing is not None:
            return existing

        exit_stack = AsyncExitStack()
        if config.transport == MCPTransport.STDIO:
            read_stream, write_stream = await exit_stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config.command,
                        args=config.args,
                        env=config.env or None,
                    )
                )
            )
        else:
            read_stream, write_stream, _ = await exit_stack.enter_async_context(
                streamablehttp_client(
                    config.url,
                    headers=config.headers or None,
                    timeout=config.timeout_seconds,
                )
            )

        session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        listed_tools = await session.list_tools()
        tool_specs = {
            tool.name: self._tool_spec(config.server_id, tool)
            for tool in listed_tools.tools
        }
        connection = _ActiveMCPConnection(
            config=config,
            session=session,
            exit_stack=exit_stack,
            tools=tool_specs,
        )
        self._connections[config.server_id] = connection
        return connection

    def _build_executor(self, server_id: str, tool_name: str):
        async def execute(arguments: dict[str, JsonValue]) -> ActionResult:
            connection = self._connections[server_id]
            result = await connection.session.call_tool(
                tool_name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=connection.config.timeout_seconds),
            )
            return self._action_result(server_id, tool_name, result)

        return execute

    def _tool_spec(self, server_id: str, tool: Tool) -> ToolSpec:
        schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
        required_arguments = schema.get("required", [])
        if not isinstance(required_arguments, list):
            required_arguments = []
        return ToolSpec(
            name=tool.name,
            description=tool.description or tool.title or f"MCP tool {tool.name}",
            required_arguments=[str(item) for item in required_arguments],
            source_type=ToolSourceType.MCP,
            source_id=f"mcp:{server_id}:{tool.name}",
            metadata={
                "server_id": server_id,
                "transport": (
                    self._connections[server_id].config.transport.value
                    if server_id in self._connections
                    else ""
                ),
                "input_schema": schema,
            },
        )

    def _action_result(
        self,
        server_id: str,
        tool_name: str,
        result: CallToolResult,
    ) -> ActionResult:
        text_content = _flatten_tool_content(result)
        status = OutcomeStatus.BLOCKED_FAILURE if result.isError else OutcomeStatus.SUCCESS
        summary = text_content or (
            f"MCP tool {tool_name} on {server_id} returned structured content only."
        )
        return ActionResult(
            status=status,
            summary=summary,
            raw={
                "server_id": server_id,
                "tool_name": tool_name,
                "content": _serialize_tool_content(result.content),
                "structured_content": _json_safe(result.structuredContent),
                "is_error": result.isError,
            },
        )


def _flatten_tool_content(result: CallToolResult) -> str:
    chunks: list[str] = []
    for item in result.content:
        text = getattr(item, "text", "")
        if text:
            chunks.append(" ".join(str(text).split()))
            continue
        data = getattr(item, "data", None)
        if data is not None:
            chunks.append(str(data))
    return " ".join(chunk for chunk in chunks if chunk).strip()


def _serialize_tool_content(content: object) -> list[dict[str, JsonValue]]:
    serialized: list[dict[str, JsonValue]] = []
    if not isinstance(content, list):
        return serialized
    for item in content:
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            serialized.append(_json_safe(model_dump(mode="json")))
            continue
        serialized.append({"value": str(item)})
    return serialized


def _json_safe(value: object) -> dict[str, JsonValue] | JsonValue:
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
