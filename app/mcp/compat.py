from __future__ import annotations

from app.core.outcomes import OutcomeStatus
from app.mcp.registry import CapabilityRegistry
from app.mcp.schemas import ActionRequest, ActionResult


class MCPCompatLayer:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    async def execute(self, request: ActionRequest) -> ActionResult:
        descriptor = self.registry.resolve(request.capability)
        if descriptor is None:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=f"Capability '{request.capability}' is not registered.",
            )

        missing_arguments = [
            name
            for name in descriptor.required_arguments
            if not str(request.arguments.get(name, "")).strip()
        ]
        if missing_arguments:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=(
                    f"Capability '{descriptor.name}' is missing required arguments: "
                    f"{', '.join(missing_arguments)}."
                ),
                raw={"arguments": request.arguments},
            )

        executor = self.registry.resolve_executor(descriptor.name)
        if executor is None:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=f"Capability '{descriptor.name}' does not have an executor.",
                raw={"arguments": request.arguments},
            )

        try:
            return await executor(request.arguments)
        except Exception as exc:
            return ActionResult(
                status=OutcomeStatus.RETRYABLE_FAILURE,
                summary=f"Capability '{descriptor.name}' failed during execution.",
                raw={
                    "arguments": request.arguments,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
