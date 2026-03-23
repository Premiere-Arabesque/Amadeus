from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.types import JsonValue
from app.mcp.schemas import ActionResult, CapabilityDescriptor

type CapabilityExecutor = Callable[[dict[str, JsonValue]], Awaitable[ActionResult]]


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._executors: dict[str, CapabilityExecutor] = {}

    def register(
        self,
        descriptor: CapabilityDescriptor,
        executor: CapabilityExecutor,
    ) -> None:
        self._capabilities[descriptor.name] = descriptor
        self._executors[descriptor.name] = executor

    def resolve(self, capability: str) -> CapabilityDescriptor | None:
        return self._capabilities.get(capability)

    def resolve_executor(self, capability: str) -> CapabilityExecutor | None:
        return self._executors.get(capability)

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        return list(self._capabilities.values())
