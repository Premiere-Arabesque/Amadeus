from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

import httpx
from mcp.types import CallToolResult, Tool
from pydantic import BaseModel, Field
from pydantic_core import SchemaValidator, core_schema
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue
from app.tool.models import (
    ActionResult,
    ToolCollectionSpec,
    ToolCollectionType,
    ToolSourceType,
    ToolSpec,
)
from app.tool.registry import ToolRegistry

_ANY_VALIDATOR = SchemaValidator(core_schema.any_schema())


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


class _MCPListToolsResult(BaseModel):
    tools: list[Tool] = Field(default_factory=list)


class _JsonRpcError(BaseModel):
    code: int
    message: str


class _JsonRpcEnvelope(BaseModel):
    result: JsonValue | None = None
    error: _JsonRpcError | None = None


class _JsonHttpMCPClient:
    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            headers=self._headers or None,
            timeout=timeout_seconds,
            trust_env=False,
        )
        self._next_request_id = 0
        self._session_id = ""

    async def initialize(self) -> JsonValue:
        result, response = await self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "amadeus",
                    "version": "0.1",
                },
            },
        )
        session_id = response.headers.get("mcp-session-id", "").strip()
        if not session_id:
            raise RuntimeError("MCP streamable HTTP server did not return mcp-session-id.")
        self._session_id = session_id
        await self._notify("notifications/initialized", {})
        return result

    async def list_tools(self) -> _MCPListToolsResult:
        result, _ = await self._request("tools/list", {})
        return _MCPListToolsResult.model_validate(result or {})

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue] | None = None,
        read_timeout_seconds: timedelta | None = None,
    ) -> CallToolResult:
        timeout = (
            max(read_timeout_seconds.total_seconds(), self._timeout_seconds)
            if read_timeout_seconds is not None
            else self._timeout_seconds
        )
        result, _ = await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
            timeout_seconds=timeout,
        )
        return CallToolResult.model_validate(result or {})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _notify(self, method: str, params: dict[str, JsonValue]) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        response = await self._client.post(
            self._url,
            json=payload,
            headers=self._request_headers(),
        )
        response.raise_for_status()
        self._maybe_update_session_id(response)

    async def _request(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[JsonValue | None, httpx.Response]:
        self._next_request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id,
            "method": method,
            "params": params,
        }
        response = await self._client.post(
            self._url,
            json=payload,
            headers=self._request_headers(),
            timeout=timeout_seconds or self._timeout_seconds,
        )
        response.raise_for_status()
        self._maybe_update_session_id(response)
        envelope = _JsonRpcEnvelope.model_validate(response.json())
        if envelope.error is not None:
            raise RuntimeError(
                f"MCP JSON-RPC error {envelope.error.code} for {method}: {envelope.error.message}"
            )
        return envelope.result, response

    def _request_headers(self) -> dict[str, str] | None:
        if not self._session_id:
            return self._headers or None
        headers = dict(self._headers)
        headers["mcp-session-id"] = self._session_id
        return headers

    def _maybe_update_session_id(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id", "").strip()
        if session_id:
            self._session_id = session_id


class _JsonHttpMCPToolset(AbstractToolset[None]):
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._client: _JsonHttpMCPClient | None = None
        self._entered_count = 0
        self._cached_tools: dict[str, ToolsetTool[None]] | None = None

    @property
    def id(self) -> str | None:
        return self.config.server_id

    async def __aenter__(self):
        if self._entered_count == 0:
            self._client = _JsonHttpMCPClient(
                url=self.config.url,
                headers=self.config.headers or None,
                timeout_seconds=self.config.timeout_seconds,
            )
            await self._client.initialize()
        self._entered_count += 1
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        if self._entered_count == 0:
            return None
        self._entered_count -= 1
        if self._entered_count == 0 and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._cached_tools = None
        return None

    async def get_tools(self, ctx) -> dict[str, ToolsetTool[None]]:
        del ctx
        if self._cached_tools is not None:
            return dict(self._cached_tools)
        client = self._require_client()
        listed_tools = await client.list_tools()
        self._cached_tools = {
            tool.name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.inputSchema,
                    metadata={
                        "server_id": self.config.server_id,
                        "transport": self.config.transport.value,
                        "meta": _json_safe(tool.meta),
                        "annotations": (
                            _json_safe(tool.annotations.model_dump()) if tool.annotations else None
                        ),
                        "output_schema": _json_safe(tool.outputSchema),
                    },
                ),
                max_retries=0,
                args_validator=_ANY_VALIDATOR,
            )
            for tool in listed_tools.tools
        }
        return dict(self._cached_tools)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx,
        tool: ToolsetTool[None],
    ) -> ActionResult:
        del ctx, tool
        client = self._require_client()
        result = await client.call_tool(
            name,
            arguments=_json_safe(tool_args),
            read_timeout_seconds=timedelta(seconds=self.config.timeout_seconds),
        )
        return _action_result(self.config.server_id, name, result)

    def registered_tool_names(self) -> list[str]:
        return sorted(self._cached_tools or {})

    def _require_client(self) -> _JsonHttpMCPClient:
        if self._client is None:
            raise RuntimeError(f"MCP toolset {self.config.server_id!r} is not connected.")
        return self._client


class _WrappedMCPToolset(AbstractToolset[None]):
    def __init__(self, config: MCPServerConfig, wrapped: AbstractToolset[Any]) -> None:
        self.config = config
        self.wrapped = wrapped
        self._cached_tools: dict[str, ToolsetTool[Any]] | None = None

    @property
    def id(self) -> str | None:
        return self.config.server_id

    async def __aenter__(self):
        await self.wrapped.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        self._cached_tools = None
        return await self.wrapped.__aexit__(*args)

    async def get_tools(self, ctx) -> dict[str, ToolsetTool[None]]:
        tools = await self.wrapped.get_tools(ctx)
        self._cached_tools = tools
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx,
        tool: ToolsetTool[None],
    ) -> ActionResult:
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        return _result_to_action_result(self.config.server_id, name, result)

    def registered_tool_names(self) -> list[str]:
        return sorted(self._cached_tools or {})


@dataclass
class _ActiveMCPConnection:
    config: MCPServerConfig
    toolset: _JsonHttpMCPToolset
    registered_tools: list[str] = field(default_factory=list)


class MCPProvider:
    def __init__(self, servers: list[MCPServerConfig] | None = None) -> None:
        self.servers = list(servers or [])
        self._connections: dict[str, _ActiveMCPConnection] = {}

    async def register_tools(self, registry: ToolRegistry) -> ToolRegistry:
        for server in self.servers:
            connection = await self._connect_server(server)
            await registry.register_toolset(
                ToolCollectionSpec(
                    collection_id=f"mcp:{server.server_id}",
                    name=server.server_id,
                    description=f"MCP server {server.server_id}",
                    collection_type=ToolCollectionType.MCP_SERVER,
                    source_type=ToolSourceType.MCP,
                    source_id=f"mcp:{server.server_id}",
                    metadata={
                        "server_id": server.server_id,
                        "transport": server.transport.value,
                        "url": server.url,
                    },
                ),
                connection.toolset,
            )
            connection.registered_tools = connection.toolset.registered_tool_names()
        return registry

    async def close(self) -> None:
        for connection in self._connections.values():
            await connection.toolset.__aexit__(None, None, None)
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
            registered_tools = connection.registered_tools if connection is not None else []
            statuses.append(
                {
                    "server_id": server.server_id,
                    "transport": server.transport.value,
                    "connected": connection is not None,
                    "registered_tools": sorted(registered_tools),
                    "tool_count": len(registered_tools),
                }
            )
        return statuses

    def _tool_spec(self, server_id: str, tool: Tool):
        return _tool_spec(server_id, tool)

    def _action_result(self, server_id: str, tool_name: str, result: object) -> ActionResult:
        return _result_to_action_result(server_id, tool_name, result)

    async def _connect_server(self, config: MCPServerConfig) -> _ActiveMCPConnection:
        existing = self._connections.get(config.server_id)
        if existing is not None:
            return existing

        if config.transport == MCPTransport.STDIO:
            toolset = _WrappedMCPToolset(
                config,
                MCPServerStdio(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                    id=config.server_id,
                    timeout=config.timeout_seconds,
                    read_timeout=max(config.timeout_seconds, 300.0),
                ),
            )
        elif config.transport == MCPTransport.STREAMABLE_HTTP:
            toolset = _JsonHttpMCPToolset(config)
        else:
            raise RuntimeError(
                f"Unsupported MCP transport {config.transport.value!r} for server {config.server_id!r}."
            )
        await toolset.__aenter__()
        connection = _ActiveMCPConnection(
            config=config,
            toolset=toolset,
            registered_tools=toolset.registered_tool_names(),
        )
        self._connections[config.server_id] = connection
        return connection


def _action_result(
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


def _result_to_action_result(
    server_id: str,
    tool_name: str,
    result: object,
) -> ActionResult:
    if isinstance(result, ActionResult):
        return result
    if isinstance(result, CallToolResult):
        return _action_result(server_id, tool_name, result)
    summary = _generic_tool_summary(result)
    return ActionResult(
        status=OutcomeStatus.SUCCESS,
        summary=summary,
        raw={
            "server_id": server_id,
            "tool_name": tool_name,
            "result": _json_safe(result),
        },
    )


def _tool_spec(server_id: str, tool: Tool):
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
        collection_id=f"mcp:{server_id}",
        collection_name=server_id,
        collection_type=ToolCollectionType.MCP_SERVER,
        metadata={
            "server_id": server_id,
            "input_schema": schema,
        },
    )


def _generic_tool_summary(result: object) -> str:
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return str(result)


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
