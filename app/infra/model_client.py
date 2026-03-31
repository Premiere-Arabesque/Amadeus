from __future__ import annotations

import inspect
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider_class

from app.core.types import JsonValue, utc_now
from app.infra.settings import ModelRole, ModelRoute, ModelRoutingSettings

StructuredT = TypeVar("StructuredT", bound=BaseModel)


class ModelTraceHTTPRequest(BaseModel):
    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class ModelTraceHTTPResponse(BaseModel):
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class ModelTraceHTTPExchange(BaseModel):
    request: ModelTraceHTTPRequest
    response: ModelTraceHTTPResponse | None = None


class ModelTracePayload(BaseModel):
    recorded_at: str = Field(default_factory=lambda: utc_now().isoformat())
    request_kind: str
    role: str
    provider: str
    model: str
    base_url: str | None = None
    schema_name: str | None = None
    prompt: str
    system_prompt: str = ""
    model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    provider_name: str | None = None
    provider_details: dict[str, JsonValue] = Field(default_factory=dict)
    output_text: str | None = None
    output_object: JsonValue | None = None
    fallback_strategy: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    http_exchanges: list[ModelTraceHTTPExchange] = Field(default_factory=list)


_current_http_trace_collector: ContextVar["_HTTPTraceCollector | None"] = ContextVar(
    "amadeus_http_trace_collector",
    default=None,
)


@dataclass(slots=True)
class ModelRequest:
    role: ModelRole
    route: ModelRoute
    prompt: str
    system_prompt: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    extra_settings: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TextResponse:
    text: str
    provider_name: str | None = None
    provider_details: dict[str, Any] | None = None


@dataclass(slots=True)
class StructuredResponse[StructuredT]:
    structured: StructuredT
    provider_name: str | None = None
    provider_details: dict[str, Any] | None = None


class ModelRouter:
    def __init__(self, settings: ModelRoutingSettings) -> None:
        self.settings = settings

    def resolve(self, role: ModelRole) -> ModelRoute:
        return getattr(self.settings, role.value)

    def build_request(
        self,
        role: ModelRole,
        *,
        prompt: str,
        system_prompt: str = "",
        extra_settings: dict[str, object] | None = None,
    ) -> ModelRequest:
        return ModelRequest(
            role=role,
            route=self.resolve(role),
            prompt=prompt,
            system_prompt=system_prompt,
            extra_settings=dict(extra_settings or {}),
        )


class ModelClient:
    async def generate_text(self, request: ModelRequest) -> TextResponse:
        raise NotImplementedError

    async def generate_structured(
        self,
        request: ModelRequest,
        schema_type: type[StructuredT],
    ) -> StructuredResponse[StructuredT]:
        raise NotImplementedError


class PydanticAIModelClient(ModelClient):
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._trace_sink: Any = None
        self.http_client = self._instrument_http_client(
            http_client or httpx.AsyncClient(trust_env=False)
        )

    def bind_trace_sink(self, sink) -> None:
        self._trace_sink = sink

    async def generate_text(self, request: ModelRequest) -> TextResponse:
        if not request.route.is_configured():
            raise RuntimeError("No configured model route is available for this request.")

        agent = Agent(
            model=self._build_model(request.route),
            system_prompt=request.system_prompt or (),
            output_type=str,
        )
        model_settings = _build_model_settings(request)
        collector = _HTTPTraceCollector()
        token = _current_http_trace_collector.set(collector)
        started_at = perf_counter()
        try:
            result = await agent.run(
                request.prompt,
                model_settings=model_settings,
            )
        except Exception as exc:
            _current_http_trace_collector.reset(token)
            self._emit_trace(
                self._build_trace_payload(
                    request=request,
                    request_kind="text",
                    schema_name=None,
                    model_settings=model_settings,
                    provider_name=None,
                    provider_details=None,
                    output_text=None,
                    output_object=None,
                    fallback_strategy=None,
                    error=str(exc),
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    collector=collector,
                )
            )
            raise
        _current_http_trace_collector.reset(token)
        provider_name, provider_details = _extract_response_metadata(result.response)
        provider_details = _augment_provider_details_from_http(
            provider_name=provider_name,
            provider_details=provider_details,
            collector=collector,
        )
        self._emit_trace(
            self._build_trace_payload(
                request=request,
                request_kind="text",
                schema_name=None,
                model_settings=model_settings,
                provider_name=provider_name,
                provider_details=provider_details,
                output_text=result.output,
                output_object=None,
                fallback_strategy=None,
                error=None,
                duration_ms=int((perf_counter() - started_at) * 1000),
                collector=collector,
            )
        )
        return TextResponse(
            text=result.output,
            provider_name=provider_name,
            provider_details=provider_details,
        )

    async def generate_structured(
        self,
        request: ModelRequest,
        schema_type: type[StructuredT],
    ) -> StructuredResponse[StructuredT]:
        if not request.route.is_configured():
            raise RuntimeError("No configured model route is available for this request.")

        agent = Agent(
            model=self._build_model(request.route),
            system_prompt=request.system_prompt or (),
            output_type=schema_type,
        )
        model_settings = _build_model_settings(request)
        collector = _HTTPTraceCollector()
        token = _current_http_trace_collector.set(collector)
        started_at = perf_counter()
        fallback_strategy: str | None = None
        try:
            result = await agent.run(
                request.prompt,
                model_settings=model_settings,
            )
        except Exception as exc:
            if _should_retry_structured_as_json(request.route, exc):
                fallback_strategy = "json_text_fallback"
                result, structured_output = await self._run_structured_json_fallback(
                    request=request,
                    schema_type=schema_type,
                    model_settings=model_settings,
                )
            else:
                _current_http_trace_collector.reset(token)
                self._emit_trace(
                    self._build_trace_payload(
                        request=request,
                        request_kind="structured",
                        schema_name=schema_type.__name__,
                        model_settings=model_settings,
                        provider_name=None,
                        provider_details=None,
                        output_text=None,
                        output_object=None,
                        fallback_strategy=None,
                        error=str(exc),
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        collector=collector,
                    )
                )
                raise
        else:
            structured_output = schema_type.model_validate(result.output)
        _current_http_trace_collector.reset(token)
        provider_name, provider_details = _extract_response_metadata(result.response)
        provider_details = _augment_provider_details_from_http(
            provider_name=provider_name,
            provider_details=provider_details,
            collector=collector,
        )
        self._emit_trace(
            self._build_trace_payload(
                request=request,
                request_kind="structured",
                schema_name=schema_type.__name__,
                model_settings=model_settings,
                provider_name=provider_name,
                provider_details=provider_details,
                output_text=None,
                output_object=_json_safe_payload(structured_output),
                fallback_strategy=fallback_strategy,
                error=None,
                duration_ms=int((perf_counter() - started_at) * 1000),
                collector=collector,
            )
        )
        return StructuredResponse(
            structured=structured_output,
            provider_name=provider_name,
            provider_details=provider_details,
        )

    def _build_model(self, route: ModelRoute):
        model_identifier = _build_model_identifier(route)
        try:
            return infer_model(
                model_identifier,
                provider_factory=lambda provider_name: self._build_provider(
                    provider_name,
                    route,
                ),
            )
        except Exception as exc:  # pragma: no cover - exact provider errors vary by SDK
            raise RuntimeError(
                f"Failed to configure PydanticAI model route for provider "
                f"{route.normalized_provider()!r}: {exc}"
            ) from exc

    def _build_provider(self, provider_name: str, route: ModelRoute):
        provider_class = infer_provider_class(provider_name)
        kwargs = _build_provider_kwargs(
            provider_class,
            provider_name=provider_name,
            route=route,
            http_client=self.http_client,
        )
        return provider_class(**kwargs)

    def _instrument_http_client(self, client: httpx.AsyncClient) -> httpx.AsyncClient:
        if getattr(client, "_amadeus_trace_installed", False):
            return client
        client.event_hooks.setdefault("request", []).append(self._capture_request_hook)
        client.event_hooks.setdefault("response", []).append(self._capture_response_hook)
        setattr(client, "_amadeus_trace_installed", True)
        return client

    async def _capture_request_hook(self, request: httpx.Request) -> None:
        collector = _current_http_trace_collector.get()
        if collector is None:
            return
        collector.record_request(request)

    async def _capture_response_hook(self, response: httpx.Response) -> None:
        collector = _current_http_trace_collector.get()
        if collector is None:
            return
        await collector.record_response(response)

    def _build_trace_payload(
        self,
        *,
        request: ModelRequest,
        request_kind: str,
        schema_name: str | None,
        model_settings: dict[str, object],
        provider_name: str | None,
        provider_details: dict[str, Any] | None,
        output_text: str | None,
        output_object: JsonValue | None,
        fallback_strategy: str | None,
        error: str | None,
        duration_ms: int,
        collector: "_HTTPTraceCollector",
    ) -> ModelTracePayload:
        details = provider_details if isinstance(provider_details, dict) else {}
        return ModelTracePayload(
            request_kind=request_kind,
            role=request.role.value,
            provider=request.route.normalized_provider(),
            model=request.route.model,
            base_url=request.route.base_url,
            schema_name=schema_name,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            model_settings=_json_safe_dict(model_settings),
            provider_name=provider_name,
            provider_details=_json_safe_dict(details),
            output_text=output_text,
            output_object=output_object,
            fallback_strategy=fallback_strategy,
            error=error,
            duration_ms=duration_ms,
            http_exchanges=collector.exchanges,
        )

    def _emit_trace(self, trace: ModelTracePayload) -> None:
        if not callable(self._trace_sink):
            return
        try:
            self._trace_sink(trace)
        except Exception:
            return

    async def _run_structured_json_fallback(
        self,
        *,
        request: ModelRequest,
        schema_type: type[StructuredT],
        model_settings: dict[str, object],
    ) -> tuple[Any, StructuredT]:
        fallback_agent = Agent(
            model=self._build_model(request.route),
            system_prompt=_json_text_system_prompt(
                base_system_prompt=request.system_prompt,
                schema_type=schema_type,
            ),
            output_type=str,
        )
        result = await fallback_agent.run(
            _json_text_user_prompt(
                prompt=request.prompt,
                schema_type=schema_type,
            ),
            model_settings=model_settings,
        )
        payload = _extract_json_payload(result.output)
        structured_output = schema_type.model_validate(payload)
        return result, structured_output


class _HTTPTraceCollector:
    def __init__(self) -> None:
        self.exchanges: list[ModelTraceHTTPExchange] = []

    def record_request(self, request: httpx.Request) -> None:
        self.exchanges.append(
            ModelTraceHTTPExchange(
                request=ModelTraceHTTPRequest(
                    method=request.method,
                    url=str(request.url),
                    headers=_redact_headers(request.headers),
                    body=_decode_body(getattr(request, "content", b"")),
                )
            )
        )

    async def record_response(self, response: httpx.Response) -> None:
        try:
            raw_body = await response.aread()
        except Exception:
            raw_body = b""
        target = next(
            (exchange for exchange in reversed(self.exchanges) if exchange.response is None),
            None,
        )
        response_payload = ModelTraceHTTPResponse(
            status_code=response.status_code,
            headers=_redact_headers(response.headers),
            body=_decode_body(raw_body),
        )
        if target is not None:
            target.response = response_payload
            return
        self.exchanges.append(
            ModelTraceHTTPExchange(
                request=ModelTraceHTTPRequest(
                    method=response.request.method,
                    url=str(response.request.url),
                    headers=_redact_headers(response.request.headers),
                    body=_decode_body(getattr(response.request, "content", b"")),
                ),
                response=response_payload,
            )
        )


def _build_model_identifier(route: ModelRoute) -> str:
    provider = route.normalized_provider()
    if provider == "custom":
        provider = "openai"
    return f"{provider}:{route.model}"


def _build_model_settings(request: ModelRequest) -> dict[str, object]:
    settings: dict[str, object] = {
        "timeout": request.route.timeout_seconds,
        "temperature": (
            request.temperature
            if request.temperature is not None
            else request.route.temperature
        ),
        "max_tokens": (
            request.max_tokens
            if request.max_tokens is not None
            else request.route.max_tokens
        ),
    }
    settings.update(request.extra_settings)
    return settings


def _extract_response_metadata(response: Any) -> tuple[str | None, dict[str, Any] | None]:
    provider_name = getattr(response, "provider_name", None)
    provider_details = getattr(response, "provider_details", None)
    if isinstance(provider_details, dict):
        return provider_name, dict(provider_details)
    return provider_name, None


def _augment_provider_details_from_http(
    *,
    provider_name: str | None,
    provider_details: dict[str, Any] | None,
    collector: "_HTTPTraceCollector",
) -> dict[str, Any] | None:
    details = dict(provider_details) if isinstance(provider_details, dict) else {}
    if provider_name != "alibaba" or details.get("logprobs"):
        return details or None

    for exchange in reversed(collector.exchanges):
        raw_body = exchange.response.body if exchange.response is not None else None
        payload = _extract_alibaba_logprobs_from_body(raw_body)
        if payload:
            details["logprobs"] = payload
            break
    return details or None


def _extract_alibaba_logprobs_from_body(body: str | None) -> list[dict[str, Any]] | None:
    if not body:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    direct_logprobs = _coerce_logprob_entries(first_choice.get("logprobs"))
    if direct_logprobs:
        return direct_logprobs

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None
    return _coerce_logprob_entries(message.get("logprobs"))


def _coerce_logprob_entries(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        value = value.get("content")
    if not isinstance(value, list) or not value:
        return None
    entries: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            entries.append(dict(item))
    return entries or None


def _build_provider_kwargs(
    provider_class: type[Any],
    *,
    provider_name: str,
    route: ModelRoute,
    http_client: httpx.AsyncClient | None,
) -> dict[str, object]:
    parameters = inspect.signature(provider_class).parameters
    kwargs: dict[str, object] = {}

    if "api_key" in parameters and route.api_key:
        kwargs["api_key"] = route.api_key
    if "base_url" in parameters and route.base_url:
        kwargs["base_url"] = _normalize_base_url(provider_name, route.base_url)
    if "http_client" in parameters and http_client is not None:
        kwargs["http_client"] = http_client

    return kwargs


def _normalize_base_url(provider_name: str, base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if provider_name.endswith("anthropic") and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def _decode_body(body: bytes | str | None) -> str | None:
    if body is None:
        return None
    if isinstance(body, str):
        return body
    if not body:
        return None
    return body.decode("utf-8", errors="replace")


def _redact_headers(headers: httpx.Headers) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "proxy-authorization", "x-api-key", "cookie"}:
            redacted[key] = "[redacted]"
            continue
        redacted[key] = value
    return redacted


def _json_safe_dict(payload: dict[str, object]) -> dict[str, JsonValue]:
    return {
        str(key): _json_safe_payload(value)
        for key, value in payload.items()
        if value is not None
    }


def _json_safe_payload(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _should_retry_structured_as_json(route: ModelRoute, exc: Exception) -> bool:
    message = str(exc).lower()
    if "tool_choice" not in message:
        return False
    if "thinking mode" in message:
        return True
    provider = route.normalized_provider()
    return provider in {"custom", "openai"} and "required" in message


def _json_text_system_prompt(
    *,
    base_system_prompt: str,
    schema_type: type[BaseModel],
) -> str:
    schema_json = json.dumps(schema_type.model_json_schema(), ensure_ascii=False, indent=2)
    instruction = (
        "Return valid JSON only. Do not call tools. Do not wrap the JSON in markdown fences.\n"
        f"Follow this JSON schema exactly:\n{schema_json}"
    )
    if not base_system_prompt.strip():
        return instruction
    return f"{base_system_prompt.strip()}\n\n{instruction}"


def _json_text_user_prompt(
    *,
    prompt: str,
    schema_type: type[BaseModel],
) -> str:
    return (
        f"{prompt}\n\n"
        f"Output must be a single JSON object matching schema `{schema_type.__name__}`. "
        "Return JSON only."
    )


def _extract_json_payload(text: str) -> JsonValue:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = _strip_code_fence(candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        return json.loads(candidate[start : end + 1])
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start >= 0 and end > start:
        return json.loads(candidate[start : end + 1])
    raise ValueError("Model did not return valid JSON during structured fallback.")


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
