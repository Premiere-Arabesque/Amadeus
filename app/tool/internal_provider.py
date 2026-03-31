from __future__ import annotations

from os import getenv
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue
from app.tool.models import ActionResult, ToolSourceType, ToolSpec
from app.tool.registry import ToolRegistry


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._in_title = False
        self.title_chunks: list[str] = []
        self.text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth > 0:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        chunk = _normalize_text(data)
        if not chunk:
            return
        if self._in_title:
            self.title_chunks.append(chunk)
            return
        if self._ignored_depth == 0:
            self.text_chunks.append(chunk)


class _DuckDuckGoHtmlSearchExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current_href = ""
        self._current_title_chunks: list[str] = []
        self._current_snippet_chunks: list[str] = []
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        href = str(attr_map.get("href") or "").strip()
        if "result__a" in classes:
            self._flush_current()
            self._current_href = _normalize_duckduckgo_result_url(href)
            self._capture_title = True
            self._capture_snippet = False
            return
        if "result__snippet" in classes:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._capture_title:
            self._capture_title = False
            return
        if self._capture_snippet:
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        chunk = _normalize_text(data)
        if not chunk:
            return
        if self._capture_title:
            self._current_title_chunks.append(chunk)
            return
        if self._capture_snippet:
            self._current_snippet_chunks.append(chunk)

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        title = _normalize_text(" ".join(self._current_title_chunks))
        snippet = _normalize_text(" ".join(self._current_snippet_chunks))
        if title and self._current_href:
            self.results.append(
                {
                    "text": title if not snippet else f"{title}: {snippet}",
                    "url": self._current_href,
                }
            )
        self._current_href = ""
        self._current_title_chunks = []
        self._current_snippet_chunks = []
        self._capture_title = False
        self._capture_snippet = False


class _BingHtmlSearchExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._result_depth = 0
        self._heading_depth = 0
        self._capture_title = False
        self._capture_snippet = False
        self._current_href = ""
        self._current_title_chunks: list[str] = []
        self._current_snippet_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        if tag == "li" and "b_algo" in classes:
            self._flush_current()
            self._result_depth = 1
            return
        if self._result_depth <= 0:
            return
        if tag == "li":
            self._result_depth += 1
            return
        if tag == "h2":
            self._heading_depth += 1
            return
        if tag == "a" and self._heading_depth > 0 and not self._current_href:
            self._current_href = str(attr_map.get("href") or "").strip()
            self._capture_title = True
            return
        if "b_caption" in classes or tag == "p":
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if self._result_depth <= 0:
            return
        if tag == "a" and self._capture_title:
            self._capture_title = False
            return
        if tag == "h2" and self._heading_depth > 0:
            self._heading_depth -= 1
            return
        if tag == "p" and self._capture_snippet:
            self._capture_snippet = False
            return
        if tag == "li":
            self._result_depth -= 1
            if self._result_depth <= 0:
                self._flush_current()

    def handle_data(self, data: str) -> None:
        chunk = _normalize_text(data)
        if not chunk or self._result_depth <= 0:
            return
        if self._capture_title:
            self._current_title_chunks.append(chunk)
            return
        if self._capture_snippet:
            self._current_snippet_chunks.append(chunk)

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        title = _normalize_text(" ".join(self._current_title_chunks))
        snippet = _normalize_text(" ".join(self._current_snippet_chunks))
        if title and self._current_href:
            text = title if not snippet else f"{title}: {snippet}"
            self.results.append({"text": text, "url": self._current_href})
        self._result_depth = 0
        self._heading_depth = 0
        self._capture_title = False
        self._capture_snippet = False
        self._current_href = ""
        self._current_title_chunks = []
        self._current_snippet_chunks = []


class ReadUrlTool:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        timeout_seconds: float = 10.0,
        max_content_chars: int = 1200,
        user_agent: str = "Amadeus/0.1.0",
        trust_env: bool | None = None,
        verify_ssl: bool | None = None,
        allow_insecure_tls_fallback: bool | None = None,
    ) -> None:
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds
        self.max_content_chars = max_content_chars
        self.user_agent = user_agent
        self.trust_env = _env_bool("AMADEUS_INTERNAL_TOOLS_TRUST_ENV", False) if trust_env is None else trust_env
        self.verify_ssl = _env_bool("AMADEUS_INTERNAL_TOOLS_VERIFY_SSL", True) if verify_ssl is None else verify_ssl
        self.allow_insecure_tls_fallback = (
            _env_bool("AMADEUS_INTERNAL_TOOLS_ALLOW_INSECURE_TLS_FALLBACK", True)
            if allow_insecure_tls_fallback is None
            else allow_insecure_tls_fallback
        )

    async def execute(self, arguments: dict[str, JsonValue]) -> ActionResult:
        url = str(arguments.get("url", "")).strip()
        if not _is_supported_url(url):
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary="The read_url tool requires a valid http or https URL.",
                raw={"arguments": arguments},
            )

        try:
            response = await self._get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return ActionResult(
                status=(
                    OutcomeStatus.RETRYABLE_FAILURE
                    if status_code >= 500
                    else OutcomeStatus.BLOCKED_FAILURE
                ),
                summary=f"Failed to read {url}: HTTP {status_code}.",
                raw={
                    "url": url,
                    "status_code": status_code,
                    "final_url": str(exc.response.url),
                },
            )
        except httpx.HTTPError as exc:
            return ActionResult(
                status=OutcomeStatus.RETRYABLE_FAILURE,
                summary=f"Failed to read {url}: {type(exc).__name__}.",
                raw={"url": url, "error": str(exc), "error_type": type(exc).__name__},
            )

        title, content = _extract_response_text(response)
        content_excerpt = _truncate_text(content, limit=self.max_content_chars)
        summary_target = title or str(response.url)
        excerpt = _truncate_text(content, limit=280) or "No readable content extracted."
        transport_flags = {"used_proxy_env": self.trust_env, "ssl_verification": "enabled"}
        if isinstance(response.extensions.get("amadeus_insecure_tls_fallback"), bool):
            if response.extensions["amadeus_insecure_tls_fallback"]:
                transport_flags["ssl_verification"] = "disabled_after_cert_failure"
        return ActionResult(
            status=OutcomeStatus.SUCCESS,
            summary=f"Read {summary_target}: {excerpt}",
            raw={
                "url": url,
                "final_url": str(response.url),
                "status_code": response.status_code,
                "content_type": _content_type(response),
                "title": title,
                "content": content_excerpt,
                "transport": transport_flags,
            },
        )

    async def _get(self, url: str) -> httpx.Response:
        request_options = {
            "follow_redirects": True,
            "headers": {"User-Agent": self.user_agent},
        }
        if self.http_client is not None:
            return await self.http_client.get(url, **request_options)

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            trust_env=self.trust_env,
            verify=self.verify_ssl,
        ) as client:
            try:
                return await client.get(url, **request_options)
            except httpx.ConnectError as exc:
                if not self._should_retry_insecure(exc):
                    raise

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            trust_env=self.trust_env,
            verify=False,
        ) as insecure_client:
            response = await insecure_client.get(url, **request_options)
            response.extensions["amadeus_insecure_tls_fallback"] = True
            return response

    def _should_retry_insecure(self, exc: httpx.ConnectError) -> bool:
        if not self.allow_insecure_tls_fallback:
            return False
        if self.http_client is not None or not self.verify_ssl:
            return False
        return "CERTIFICATE_VERIFY_FAILED" in str(exc)


class SearchWebTool:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        timeout_seconds: float = 10.0,
        user_agent: str = "Amadeus/0.1.0",
        endpoint: str = "https://api.duckduckgo.com/",
        html_fallback_endpoint: str = "https://www.bing.com/search",
        trust_env: bool | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.endpoint = endpoint
        self.html_fallback_endpoint = html_fallback_endpoint
        self.trust_env = _env_bool("AMADEUS_INTERNAL_TOOLS_TRUST_ENV", False) if trust_env is None else trust_env
        self.verify_ssl = _env_bool("AMADEUS_INTERNAL_TOOLS_VERIFY_SSL", True) if verify_ssl is None else verify_ssl

    async def execute(self, arguments: dict[str, JsonValue]) -> ActionResult:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary="The search_web tool requires a non-empty query.",
                raw={"arguments": arguments},
            )

        try:
            response = await self._get(query)
            response.raise_for_status()
            if not response.text.strip():
                raise ValueError("Empty response body.")
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return ActionResult(
                status=(
                    OutcomeStatus.RETRYABLE_FAILURE
                    if status_code >= 500
                    else OutcomeStatus.BLOCKED_FAILURE
                ),
                summary=f"Failed to search the web for {query}: HTTP {status_code}.",
                raw={"query": query, "status_code": status_code},
            )
        except ValueError as exc:
            fallback = await self._search_html_fallback(query=query, trigger_error=exc)
            if fallback is not None:
                return fallback
            return ActionResult(
                status=OutcomeStatus.RETRYABLE_FAILURE,
                summary=f"Failed to parse search results for {query}.",
                raw={"query": query, "error": str(exc), "error_type": type(exc).__name__},
            )
        except httpx.HTTPError as exc:
            fallback = await self._search_html_fallback(query=query, trigger_error=exc)
            if fallback is not None:
                return fallback
            return ActionResult(
                status=OutcomeStatus.RETRYABLE_FAILURE,
                summary=f"Failed to search the web for {query}: {type(exc).__name__}.",
                raw={"query": query, "error": str(exc), "error_type": type(exc).__name__},
            )

        if not isinstance(payload, dict):
            return ActionResult(
                status=OutcomeStatus.RETRYABLE_FAILURE,
                summary=f"Search results for {query} were not returned as a JSON object.",
                raw={"query": query},
            )

        heading = _normalize_text(str(payload.get("Heading", "")))
        abstract_text = _normalize_text(str(payload.get("AbstractText", "")))
        abstract_url = str(payload.get("AbstractURL", "")).strip()
        hits = _collect_search_hits(payload)[:5]
        summary = _search_summary(
            query=query,
            heading=heading,
            abstract_text=abstract_text,
            hits=hits,
        )
        status = OutcomeStatus.SUCCESS if abstract_text or hits else OutcomeStatus.PARTIAL_SUCCESS
        return ActionResult(
            status=status,
            summary=summary,
            raw={
                "query": query,
                "provider": "duckduckgo_instant_answer",
                "heading": heading,
                "abstract_text": abstract_text,
                "abstract_url": abstract_url,
                "results": hits,
            },
        )

    async def _get(self, query: str) -> httpx.Response:
        request_options = {
            "params": {
                "q": query,
                "format": "json",
                "no_html": "1",
                "no_redirect": "1",
                "skip_disambig": "1",
            },
            "headers": {"User-Agent": self.user_agent},
        }
        if self.http_client is not None:
            return await self.http_client.get(self.endpoint, **request_options)

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            trust_env=self.trust_env,
            verify=self.verify_ssl,
        ) as client:
            return await client.get(self.endpoint, **request_options)

    async def _search_html_fallback(
        self,
        *,
        query: str,
        trigger_error: Exception,
    ) -> ActionResult | None:
        try:
            response = await self._get_html_results(query)
            response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return None

        hits = _extract_html_search_hits(response.text, self.html_fallback_endpoint)[:5]
        if not hits:
            return None
        summary = _search_summary(query=query, heading="", abstract_text="", hits=hits)
        return ActionResult(
            status=OutcomeStatus.SUCCESS,
            summary=summary,
            raw={
                "query": query,
                "provider": _html_fallback_provider_name(self.html_fallback_endpoint),
                "results": hits,
                "fallback_reason": {
                    "error": str(trigger_error),
                    "error_type": type(trigger_error).__name__,
                },
            },
        )

    async def _get_html_results(self, query: str) -> httpx.Response:
        request_options = {
            "params": {"q": query},
            "headers": {"User-Agent": self.user_agent},
            "follow_redirects": True,
        }
        if self.http_client is not None:
            return await self.http_client.get(self.html_fallback_endpoint, **request_options)

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            trust_env=self.trust_env,
            verify=self.verify_ssl,
        ) as client:
            return await client.get(self.html_fallback_endpoint, **request_options)


class InternalProvider:
    def __init__(
        self,
        *,
        read_url_http_client: httpx.AsyncClient | None = None,
        search_web_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.read_url_tool = ReadUrlTool(http_client=read_url_http_client)
        self.search_web_tool = SearchWebTool(http_client=search_web_http_client)

    def register_tools(self, registry: ToolRegistry) -> ToolRegistry:
        registry.register(
            ToolSpec(
                name="read_url",
                description="Fetch a URL and extract readable text content.",
                required_arguments=["url"],
                source_type=ToolSourceType.INTERNAL,
                source_id="internal:read_url",
            ),
            self.read_url_tool.execute,
        )
        registry.register(
            ToolSpec(
                name="search_web",
                description="Search for external information and return structured results.",
                required_arguments=["query"],
                source_type=ToolSourceType.INTERNAL,
                source_id="internal:search_web",
            ),
            self.search_web_tool.execute,
        )
        return registry


def _is_supported_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "unknown").split(";")[0].strip().lower()


def _extract_response_text(response: httpx.Response) -> tuple[str, str]:
    body = response.text
    content_type = _content_type(response)
    if "html" not in content_type and "<html" not in body[:256].lower():
        return "", _normalize_text(body)

    extractor = _HtmlTextExtractor()
    extractor.feed(body)
    title = _normalize_text(" ".join(extractor.title_chunks))
    text = _normalize_text(" ".join(extractor.text_chunks))
    return title, text


def _normalize_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _truncate_text(value: str, *, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    truncated = normalized[: limit - 3].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}..."


def _collect_search_hits(payload: dict[str, JsonValue]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    hits.extend(_normalize_search_items(payload.get("Results", [])))
    hits.extend(_normalize_search_items(payload.get("RelatedTopics", [])))
    return hits


def _normalize_search_items(value: JsonValue) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    hits: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("Topics"), list):
            hits.extend(_normalize_search_items(item["Topics"]))
            continue

        text = _normalize_text(str(item.get("Text", "")))
        url = str(item.get("FirstURL", "")).strip()
        if text and url:
            hits.append({"text": text, "url": url})
    return hits


def _search_summary(
    *,
    query: str,
    heading: str,
    abstract_text: str,
    hits: list[dict[str, str]],
) -> str:
    target = heading or query
    if abstract_text:
        lead = _truncate_text(abstract_text, limit=260)
        if not hits:
            return f"Searched web for {target}: {lead}"
        other_hits = " | ".join(hit["text"] for hit in hits[:2])
        return f"Searched web for {target}: {lead} Other leads: {other_hits}"
    if hits:
        joined_hits = " | ".join(hit["text"] for hit in hits[:3])
        return f"Searched web for {target}: {joined_hits}"
    return f"Searched web for {target}, but found no structured results."


def _extract_duckduckgo_html_hits(html: str) -> list[dict[str, str]]:
    parser = _DuckDuckGoHtmlSearchExtractor()
    parser.feed(html)
    parser.close()
    return parser.results


def _extract_bing_html_hits(html: str) -> list[dict[str, str]]:
    parser = _BingHtmlSearchExtractor()
    parser.feed(html)
    parser.close()
    return parser.results


def _extract_html_search_hits(html: str, endpoint: str) -> list[dict[str, str]]:
    host = urlparse(endpoint).netloc.lower()
    if "bing.com" in host:
        return _extract_bing_html_hits(html)
    return _extract_duckduckgo_html_hits(html)


def _html_fallback_provider_name(endpoint: str) -> str:
    host = urlparse(endpoint).netloc.lower()
    if "bing.com" in host:
        return "bing_html"
    return "duckduckgo_html"


def _normalize_duckduckgo_result_url(value: str) -> str:
    href = value.strip()
    if not href:
        return ""
    absolute = urljoin("https://duckduckgo.com", href)
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    redirected = query.get("uddg", [])
    if redirected:
        return unquote(redirected[0]).strip()
    return absolute


def _env_bool(name: str, default: bool) -> bool:
    raw = getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
