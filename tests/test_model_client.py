import httpx
import pytest
from pydantic import BaseModel

from app.infra.model_client import ModelRequest, ModelTracePayload, PydanticAIModelClient
from app.infra.settings import ModelRole, ModelRoute


class StructuredExample(BaseModel):
    message: str


@pytest.mark.anyio
async def test_openai_compatible_generate_structured_parses_json_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mock-llm.local/v1/chat/completions"
        payload = request.read().decode("utf-8")
        assert '"tool_choice":"required"' in payload
        assert '"name":"final_result"' in payload
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "created": 123,
                "model": "planner-x",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "final_result",
                                        "arguments": (
                                            '{"message": '
                                            '"structured response from provider"}'
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = PydanticAIModelClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        response = await client.generate_structured(
            ModelRequest(
                role=ModelRole.DECISION,
                route=ModelRoute(
                    provider="custom",
                    model="planner-x",
                    base_url="https://mock-llm.local/v1",
                ),
                prompt="Plan the next small step.",
                system_prompt="Return JSON only.",
            ),
            StructuredExample,
        )
    finally:
        await client.http_client.aclose()  # type: ignore[union-attr]

    assert response.structured.message == "structured response from provider"
    assert response.provider_name == "openai"
    assert response.provider_details == {
        "finish_reason": "stop",
        "timestamp": response.provider_details["timestamp"],
    }


@pytest.mark.anyio
async def test_anthropic_generate_text_uses_pydanticai_provider_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mock-anthropic.local/v1/messages?beta=true"
        payload = request.read().decode("utf-8")
        assert '"system":"be concise"' in payload
        assert '"text":"say hi"' in payload
        return httpx.Response(
            status_code=200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "hello from anthropic"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
        )

    client = PydanticAIModelClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        response = await client.generate_text(
            ModelRequest(
                role=ModelRole.DIALOGUE,
                route=ModelRoute(
                    provider="anthropic",
                    model="claude-test",
                    api_key="test-key",
                    base_url="https://mock-anthropic.local/v1",
                ),
                prompt="say hi",
                system_prompt="be concise",
            )
        )
    finally:
        await client.http_client.aclose()  # type: ignore[union-attr]

    assert response.text == "hello from anthropic"
    assert response.provider_name == "anthropic"
    assert response.provider_details == {"finish_reason": "end_turn"}


@pytest.mark.anyio
async def test_model_client_emits_raw_trace_for_http_exchange() -> None:
    traces: list[ModelTracePayload] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mock-llm.local/v1/chat/completions"
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_trace",
                "object": "chat.completion",
                "created": 123,
                "model": "planner-x",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "trace me",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    client = PydanticAIModelClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    client.bind_trace_sink(traces.append)
    try:
        response = await client.generate_text(
            ModelRequest(
                role=ModelRole.DECISION,
                route=ModelRoute(
                    provider="custom",
                    model="planner-x",
                    base_url="https://mock-llm.local/v1",
                ),
                prompt="show me the final prompt",
                system_prompt="be transparent",
            )
        )
    finally:
        await client.http_client.aclose()  # type: ignore[union-attr]

    assert response.text == "trace me"
    assert len(traces) == 1
    trace = traces[0]
    assert trace.request_kind == "text"
    assert trace.role == "decision"
    assert trace.prompt == "show me the final prompt"
    assert trace.system_prompt == "be transparent"
    assert trace.http_exchanges[0].request.url == "https://mock-llm.local/v1/chat/completions"
    assert '"messages"' in (trace.http_exchanges[0].request.body or "")
    assert trace.http_exchanges[0].response is not None
    assert trace.http_exchanges[0].response.status_code == 200
    assert '"trace me"' in (trace.http_exchanges[0].response.body or "")


@pytest.mark.anyio
async def test_openai_compatible_structured_falls_back_to_json_text_on_tool_choice_error() -> None:
    seen_payloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode("utf-8")
        seen_payloads.append(payload)
        if '"tool_choice":"required"' in payload:
            return httpx.Response(
                status_code=400,
                json={
                    "message": (
                        "<400> InternalError.Algo.InvalidParameter: The tool_choice parameter "
                        "does not support being set to required or object in thinking mode"
                    ),
                    "type": "invalid_request_error",
                    "code": "invalid_parameter_error",
                },
            )
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_text_json",
                "object": "chat.completion",
                "created": 123,
                "model": "qwen3.5-plus",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"message":"json fallback works"}',
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 4,
                    "total_tokens": 8,
                },
            },
        )

    client = PydanticAIModelClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        response = await client.generate_structured(
            ModelRequest(
                role=ModelRole.DECISION,
                route=ModelRoute(
                    provider="custom",
                    model="qwen3.5-plus",
                    base_url="https://mock-qwen.local/v1",
                ),
                prompt="Need a structured result.",
                system_prompt="Return the expected schema.",
            ),
            StructuredExample,
        )
    finally:
        await client.http_client.aclose()  # type: ignore[union-attr]

    assert response.structured.message == "json fallback works"
    assert len(seen_payloads) == 2
    assert '"tool_choice":"required"' in seen_payloads[0]
    assert '"tool_choice":"required"' not in seen_payloads[1]
