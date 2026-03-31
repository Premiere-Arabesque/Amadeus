import asyncio
import shutil
from pathlib import Path

import pytest

from app.core.outcomes import OutcomeStatus
from app.tool.mcp_provider import MCPProvider, MCPServerConfig
from app.tool.registry import ToolRegistry

SANDBOX_DIR = Path(__file__).resolve().parent.parent / "memory" / "tests" / "mcp_filesystem_sandbox"
NPM_CACHE_DIR = Path(__file__).resolve().parent.parent / "memory" / "tests" / "npm-cache"


def test_mcp_provider_can_connect_to_real_filesystem_server() -> None:
    npx_command = shutil.which("npx.cmd") or shutil.which("npx")
    if npx_command is None:
        pytest.skip("npx is not available in the current environment.")

    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    NPM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def run_integration() -> None:
        provider = MCPProvider(
            servers=[
                MCPServerConfig(
                    server_id="filesystem-test",
                    transport="stdio",
                    command=npx_command,
                    args=[
                        "-y",
                        "@modelcontextprotocol/server-filesystem",
                        str(SANDBOX_DIR),
                    ],
                    env={
                        "npm_config_cache": str(NPM_CACHE_DIR),
                    },
                    timeout_seconds=30.0,
                )
            ]
        )
        registry = ToolRegistry()
        try:
            await provider.register_tools(registry)
            assert "list_allowed_directories" in registry.tool_names()

            result = await registry.invoke("list_allowed_directories", {})

            assert result.status == OutcomeStatus.SUCCESS
            assert str(SANDBOX_DIR) in result.summary
            assert result.raw["server_id"] == "filesystem-test"
            assert result.raw["tool_name"] == "list_allowed_directories"
            assert provider.connected_server_count() == 1
            assert provider.registered_tool_count() > 0
            assert provider.server_status()[0]["connected"] is True
        finally:
            await provider.close()

    try:
        asyncio.run(run_integration())
    except PermissionError as exc:
        pytest.skip(f"Sandbox blocked stdio subprocess pipes for MCP integration: {exc}")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 5:
            pytest.skip(f"Sandbox blocked stdio subprocess pipes for MCP integration: {exc}")
        raise
