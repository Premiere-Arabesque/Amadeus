from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic_ai import Agent

from app.core.types import ProviderName
from app.infra.settings import ModelRouteConfig, ModelRoutingSettings


@dataclass(slots=True)
class ModelToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelRequest:
    role: ModelRole
    provider: ProviderName
    model: str
    prompt: str
    system_prompt: str = ""
    api_key_env: str | None = None
    base_url: str | None = None
    tools: list[ModelToolDefinition] = field(default_factory=list)


@dataclass(slots=True)
class ModelResponse:
    provider: ProviderName
    model: str
    text: str
    structured: BaseModel | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    async def generate_text(self, request: ModelRequest) -> ModelResponse:
        ...

    async def generate_structured(
        self,
        request: ModelRequest,
        output_type: type[BaseModel],
    ) -> ModelResponse:
        ...


class ModelRole(StrEnum):
    DIALOGUE = "dialogue"
    DECISION = "decision"
    MEMORY = "memory"


class ModelRouter:
    def __init__(self, settings: ModelRoutingSettings) -> None:
        self.settings = settings

    def resolve(self, role: ModelRole) -> ModelRouteConfig:
        return getattr(self.settings, role.value)

    def build_request(
        self,
        role: ModelRole,
        prompt: str,
        system_prompt: str = "",
    ) -> ModelRequest:
        route = self.resolve(role)
        return ModelRequest(
            role=role,
            provider=route.provider,
            model=route.model,
            prompt=prompt,
            system_prompt=system_prompt,
            api_key_env=route.api_key_env,
            base_url=route.base_url,
        )


class PydanticAIModelClient:
    """Thin adapter that keeps PydanticAI out of the rest of the codebase."""

    def _model_name(self, request: ModelRequest) -> str:
        return f"{request.provider.value}:{request.model}"

    async def generate_text(self, request: ModelRequest) -> ModelResponse:
        agent = Agent(
            self._model_name(request),
            instructions=request.system_prompt or "Be concise and structured.",
        )
        result = await agent.run(request.prompt)
        return ModelResponse(
            provider=request.provider,
            model=request.model,
            text=str(result.output),
            raw={
                "framework": "pydantic_ai",
                "role": request.role.value,
                "api_key_env": request.api_key_env,
                "base_url": request.base_url,
            },
        )

    async def generate_structured(
        self,
        request: ModelRequest,
        output_type: type[BaseModel],
    ) -> ModelResponse:
        agent = Agent(
            self._model_name(request),
            instructions=request.system_prompt or "Return a valid structured response.",
            output_type=output_type,
        )
        result = await agent.run(request.prompt)
        return ModelResponse(
            provider=request.provider,
            model=request.model,
            text=str(result.output),
            structured=result.output,
            raw={
                "framework": "pydantic_ai",
                "role": request.role.value,
                "api_key_env": request.api_key_env,
                "base_url": request.base_url,
            },
        )
