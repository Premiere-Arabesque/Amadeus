from __future__ import annotations

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue
from app.tool.models import ActionResult, ToolExecutor, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, ToolExecutor] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def get_descriptor(self, capability: str) -> ToolSpec | None:
        return self.get_tool(capability)

    def get_executor(self, name: str) -> ToolExecutor | None:
        return self._executors.get(name)

    def tool_names(self) -> list[str]:
        return sorted(self._specs)

    def capability_names(self) -> list[str]:
        return self.tool_names()

    def list_tools(self) -> list[ToolSpec]:
        return [self._specs[name] for name in self.tool_names()]

    async def invoke(self, name: str, arguments: dict[str, JsonValue]) -> ActionResult:
        spec = self.get_tool(name)
        executor = self.get_executor(name)
        if spec is None or executor is None:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=f"The tool {name!r} is not registered.",
                raw={"tool": name, "arguments": arguments},
            )

        missing = [
            argument
            for argument in spec.required_arguments
            if argument not in arguments or arguments[argument] in {"", None}
        ]
        if missing:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=(
                    f"The tool {name!r} is missing required arguments: "
                    f"{', '.join(missing)}."
                ),
                raw={"tool": name, "arguments": arguments, "missing": missing},
            )

        return await executor(arguments)
