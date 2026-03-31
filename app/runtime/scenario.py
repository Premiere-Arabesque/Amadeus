from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.communication.channels import OutboundMessage
from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome
from app.core.types import JsonValue
from app.runtime.inspection import RuntimeStateSnapshot, build_runtime_snapshot
from app.runtime.orchestrator import RuntimeOrchestrator


class ScenarioAction(StrEnum):
    SET_CLOCK = "set_clock"
    ADVANCE_CLOCK = "advance_clock"
    ENQUEUE_EVENT = "enqueue_event"
    RUN_ONCE = "run_once"
    RUN_UNTIL_IDLE = "run_until_idle"


class ScenarioStep(BaseModel):
    action: ScenarioAction
    label: str | None = None
    at: datetime | None = None
    minutes: int = 0
    event_type: EventType | None = None
    source: EventSource = EventSource.USER
    correlation_id: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    max_iterations: int = Field(default=20, ge=1, le=200)

    @model_validator(mode="after")
    def validate_action_fields(self) -> ScenarioStep:
        if self.action == ScenarioAction.SET_CLOCK and not self.at:
            raise ValueError("`at` is required when action is `set_clock`.")
        if self.action == ScenarioAction.ENQUEUE_EVENT and self.event_type is None:
            raise ValueError("`event_type` is required when action is `enqueue_event`.")
        return self


class ScenarioTraceEntry(BaseModel):
    action: ScenarioAction
    label: str | None = None
    clock_at: str
    injected_event_id: str | None = None
    outcomes: list[ActionOutcome] = Field(default_factory=list)
    outbound_messages: list[OutboundMessage] = Field(default_factory=list)
    state: RuntimeStateSnapshot


class ScenarioReplayResult(BaseModel):
    trace: list[ScenarioTraceEntry] = Field(default_factory=list)
    state: RuntimeStateSnapshot


class ScenarioRunner:
    def __init__(
        self,
        *,
        orchestrator: RuntimeOrchestrator,
        communication_hub: CommunicationHub,
    ) -> None:
        self.orchestrator = orchestrator
        self.communication_hub = communication_hub

    async def set_clock(
        self,
        at: datetime,
        *,
        label: str | None = None,
    ) -> ScenarioTraceEntry:
        self.orchestrator.set_time(_parse_datetime(at))
        return self._record(
            action=ScenarioAction.SET_CLOCK,
            label=label,
        )

    async def advance_clock(
        self,
        *,
        minutes: int,
        label: str | None = None,
    ) -> ScenarioTraceEntry:
        self.orchestrator.advance_time(minutes=minutes)
        return self._record(
            action=ScenarioAction.ADVANCE_CLOCK,
            label=label,
        )

    async def enqueue_event(
        self,
        *,
        event_type: EventType,
        source: EventSource,
        payload: dict[str, JsonValue] | None = None,
        correlation_id: str | None = None,
        label: str | None = None,
    ) -> ScenarioTraceEntry:
        event = RuntimeEvent(
            event_type=event_type,
            source=source,
            created_at=self.orchestrator.now().isoformat(),
            correlation_id=correlation_id,
            payload=payload or {},
        )
        await self.orchestrator.enqueue(event)
        return self._record(
            action=ScenarioAction.ENQUEUE_EVENT,
            label=label,
            injected_event_id=event.event_id,
        )

    async def run_once(self, *, label: str | None = None) -> ScenarioTraceEntry:
        outcome = await self.orchestrator.run_once()
        return self._record(
            action=ScenarioAction.RUN_ONCE,
            label=label,
            outcomes=[outcome] if outcome is not None else [],
            outbound_messages=self.communication_hub.drain_outbox(),
        )

    async def run_until_idle(
        self,
        *,
        max_iterations: int = 20,
        label: str | None = None,
    ) -> ScenarioTraceEntry:
        outcomes: list[ActionOutcome] = []
        outbound_messages: list[OutboundMessage] = []
        for _ in range(max_iterations):
            outcome = await self.orchestrator.run_once()
            if outcome is None:
                return self._record(
                    action=ScenarioAction.RUN_UNTIL_IDLE,
                    label=label,
                    outcomes=outcomes,
                    outbound_messages=outbound_messages,
                )
            outcomes.append(outcome)
            outbound_messages.extend(self.communication_hub.drain_outbox())
        raise RuntimeError(
            f"Scenario runner did not go idle after {max_iterations} iterations."
        )

    async def replay(self, steps: list[ScenarioStep]) -> ScenarioReplayResult:
        trace: list[ScenarioTraceEntry] = []
        for step in steps:
            if step.action == ScenarioAction.SET_CLOCK:
                trace.append(
                    await self.set_clock(
                        step.at or self.orchestrator.now(),
                        label=step.label,
                    )
                )
                continue
            if step.action == ScenarioAction.ADVANCE_CLOCK:
                trace.append(
                    await self.advance_clock(
                        minutes=step.minutes,
                        label=step.label,
                    )
                )
                continue
            if step.action == ScenarioAction.ENQUEUE_EVENT:
                trace.append(
                    await self.enqueue_event(
                        event_type=step.event_type or EventType.SYSTEM_BOOT,
                        source=step.source,
                        correlation_id=step.correlation_id,
                        payload=step.payload,
                        label=step.label,
                    )
                )
                continue
            if step.action == ScenarioAction.RUN_ONCE:
                trace.append(await self.run_once(label=step.label))
                continue
            trace.append(
                await self.run_until_idle(
                    max_iterations=step.max_iterations,
                    label=step.label,
                )
            )
        return ScenarioReplayResult(
            trace=trace,
            state=build_runtime_snapshot(
                state=self.orchestrator.state,
                orchestrator=self.orchestrator,
            ),
        )

    def _record(
        self,
        *,
        action: ScenarioAction,
        label: str | None,
        injected_event_id: str | None = None,
        outcomes: list[ActionOutcome] | None = None,
        outbound_messages: list[OutboundMessage] | None = None,
    ) -> ScenarioTraceEntry:
        return ScenarioTraceEntry(
            action=action,
            label=label,
            clock_at=self.orchestrator.now().isoformat(),
            injected_event_id=injected_event_id,
            outcomes=outcomes or [],
            outbound_messages=outbound_messages or [],
            state=build_runtime_snapshot(
                state=self.orchestrator.state,
                orchestrator=self.orchestrator,
            ),
        )


def _parse_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
