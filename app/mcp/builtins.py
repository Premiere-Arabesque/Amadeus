from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue
from app.mcp.registry import CapabilityRegistry
from app.mcp.schemas import ActionResult, CapabilityDescriptor


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._in_title = False
        self.title_chunks: list[str] = []
        self.text_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
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


class ReadUrlCapability:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        timeout_seconds: float = 10.0,
        max_content_chars: int = 1200,
        user_agent: str = "Amadeus/0.1.0",
    ) -> None:
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds
        self.max_content_chars = max_content_chars
        self.user_agent = user_agent

    async def execute(self, arguments: dict[str, JsonValue]) -> ActionResult:
        url = str(arguments.get("url", "")).strip()
        if not _is_supported_url(url):
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary="The read_url capability requires a valid http or https URL.",
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
            },
        )

    async def _get(self, url: str) -> httpx.Response:
        request_options = {
            "follow_redirects": True,
            "headers": {"User-Agent": self.user_agent},
        }
        if self.http_client is not None:
            return await self.http_client.get(url, **request_options)

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.get(url, **request_options)


class SearchWebCapability:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        timeout_seconds: float = 10.0,
        user_agent: str = "Amadeus/0.1.0",
        endpoint: str = "https://api.duckduckgo.com/",
    ) -> None:
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.endpoint = endpoint

    async def execute(self, arguments: dict[str, JsonValue]) -> ActionResult:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary="The search_web capability requires a non-empty query.",
                raw={"arguments": arguments},
            )

        try:
            response = await self._get(query)
            response.raise_for_status()
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
            return ActionResult(
                status=OutcomeStatus.RETRYABLE_FAILURE,
                summary=f"Failed to parse search results for {query}.",
                raw={"query": query, "error": str(exc), "error_type": type(exc).__name__},
            )
        except httpx.HTTPError as exc:
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

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.get(self.endpoint, **request_options)


def register_builtin_capabilities(
    registry: CapabilityRegistry,
    *,
    read_url_http_client: httpx.AsyncClient | None = None,
    search_web_http_client: httpx.AsyncClient | None = None,
) -> CapabilityRegistry:
    read_url = ReadUrlCapability(http_client=read_url_http_client)
    search_web = SearchWebCapability(http_client=search_web_http_client)
    registry.register(
        CapabilityDescriptor(
            name="read_url",
            description="Fetch a URL and extract readable text content.",
            required_arguments=["url"],
        ),
        read_url.execute,
    )
    registry.register(
        CapabilityDescriptor(
            name="search_web",
            description="Search for external information and return structured results.",
            required_arguments=["query"],
        ),
        search_web.execute,
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
