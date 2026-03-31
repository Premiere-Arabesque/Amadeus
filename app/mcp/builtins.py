from __future__ import annotations

import httpx

from app.mcp.registry import CapabilityRegistry
from app.tool.internal_provider import InternalProvider, ReadUrlTool, SearchWebTool

ReadUrlCapability = ReadUrlTool
SearchWebCapability = SearchWebTool


def register_builtin_capabilities(
    registry: CapabilityRegistry,
    *,
    read_url_http_client: httpx.AsyncClient | None = None,
    search_web_http_client: httpx.AsyncClient | None = None,
) -> CapabilityRegistry:
    provider = InternalProvider(
        read_url_http_client=read_url_http_client,
        search_web_http_client=search_web_http_client,
    )
    provider.register_tools(registry)
    return registry
