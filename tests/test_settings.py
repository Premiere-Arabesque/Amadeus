from app.core.types import ProviderName
from app.infra.model_client import ModelRole, ModelRouter
from app.infra.settings import ModelRoutingSettings


def test_model_routing_defaults() -> None:
    settings = ModelRoutingSettings()
    router = ModelRouter(settings)

    dialogue_request = router.build_request(ModelRole.DIALOGUE, prompt="hello")
    decision_request = router.build_request(ModelRole.DECISION, prompt="should we replan?")
    memory_request = router.build_request(ModelRole.MEMORY, prompt="compress memory")

    assert dialogue_request.provider == ProviderName.ANTHROPIC
    assert dialogue_request.api_key_env == "ANTHROPIC_API_KEY"
    assert decision_request.provider == ProviderName.OPENAI
    assert decision_request.api_key_env == "OPENAI_API_KEY"
    assert memory_request.role == ModelRole.MEMORY
