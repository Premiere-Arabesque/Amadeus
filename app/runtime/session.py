from __future__ import annotations

from dataclasses import dataclass

from app.memory.service import MemoryService
from app.persona.service import PersonaService
from app.runtime.orchestrator import RuntimeOrchestrator
from app.runtime.scenario import ScenarioRunner


@dataclass
class RuntimeSession:
    persona_key: str | None
    persona_service: PersonaService
    memory_service: MemoryService
    orchestrator: RuntimeOrchestrator
    scenario_runner: ScenarioRunner
