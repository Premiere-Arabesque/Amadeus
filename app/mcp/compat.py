from __future__ import annotations

from app.core.types import JsonValue
from app.mcp.registry import CapabilityRegistry
from app.mcp.schemas import ActionResult


class MCPCompatLayer:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    async def call(self, capability: str, arguments: dict[str, JsonValue]) -> ActionResult:
        return await self.registry.invoke(capability, arguments)
