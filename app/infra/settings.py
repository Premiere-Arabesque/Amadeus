from __future__ import annotations

import os

from pydantic import BaseModel, Field

from app.core.types import ProviderName


class ModelRouteConfig(BaseModel):
    provider: ProviderName
    model: str = ""
    api_key_env: str
    base_url: str | None = None


class ModelRoutingSettings(BaseModel):
    dialogue: ModelRouteConfig = Field(
        default_factory=lambda: ModelRouteConfig(
            provider=ProviderName.ANTHROPIC,
            model="",
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    decision: ModelRouteConfig = Field(
        default_factory=lambda: ModelRouteConfig(
            provider=ProviderName.OPENAI,
            model="",
            api_key_env="OPENAI_API_KEY",
        )
    )
    memory: ModelRouteConfig = Field(
        default_factory=lambda: ModelRouteConfig(
            provider=ProviderName.OPENAI,
            model="",
            api_key_env="OPENAI_API_KEY",
        )
    )

    @classmethod
    def from_env(cls) -> ModelRoutingSettings:
        return cls(
            dialogue=ModelRouteConfig(
                provider=ProviderName(os.getenv("AMADEUS_DIALOGUE_PROVIDER", "anthropic")),
                model=os.getenv("AMADEUS_DIALOGUE_MODEL", ""),
                api_key_env=os.getenv("AMADEUS_DIALOGUE_API_KEY_ENV", "ANTHROPIC_API_KEY"),
                base_url=os.getenv("AMADEUS_DIALOGUE_BASE_URL"),
            ),
            decision=ModelRouteConfig(
                provider=ProviderName(os.getenv("AMADEUS_DECISION_PROVIDER", "openai")),
                model=os.getenv("AMADEUS_DECISION_MODEL", ""),
                api_key_env=os.getenv("AMADEUS_DECISION_API_KEY_ENV", "OPENAI_API_KEY"),
                base_url=os.getenv("AMADEUS_DECISION_BASE_URL"),
            ),
            memory=ModelRouteConfig(
                provider=ProviderName(os.getenv("AMADEUS_MEMORY_PROVIDER", "openai")),
                model=os.getenv("AMADEUS_MEMORY_MODEL", ""),
                api_key_env=os.getenv("AMADEUS_MEMORY_API_KEY_ENV", "OPENAI_API_KEY"),
                base_url=os.getenv("AMADEUS_MEMORY_BASE_URL"),
            ),
        )
