import httpx
from mcp.types import CallToolResult, TextContent, Tool

from app.core.outcomes import OutcomeStatus
from app.tool.internal_provider import InternalProvider, ReadUrlTool, SearchWebTool
from app.tool.mcp_provider import MCPProvider
from app.tool.models import ToolSourceType
from app.tool.registry import ToolRegistry


def test_internal_provider_registers_builtin_tools_into_high_level_registry() -> None:
    registry = ToolRegistry()

    InternalProvider().register_tools(registry)

    read_tool = registry.get_tool("read_url")
    search_tool = registry.get_tool("search_web")

    assert read_tool is not None
    assert search_tool is not None
    assert read_tool.source_type == ToolSourceType.INTERNAL
    assert search_tool.source_type == ToolSourceType.INTERNAL
    assert read_tool.required_arguments == ["url"]
    assert search_tool.required_arguments == ["query"]


def test_mcp_provider_maps_remote_tool_to_high_level_tool_spec() -> None:
    provider = MCPProvider()
    tool = Tool(
        name="fetch_notes",
        description="Fetch notes from the remote MCP server.",
        inputSchema={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    )

    spec = provider._tool_spec("notes-server", tool)

    assert spec.name == "fetch_notes"
    assert spec.source_type == ToolSourceType.MCP
    assert spec.source_id == "mcp:notes-server:fetch_notes"
    assert spec.required_arguments == ["topic"]
    assert spec.metadata["server_id"] == "notes-server"


def test_mcp_provider_maps_call_result_to_action_result() -> None:
    provider = MCPProvider()
    result = CallToolResult(
        content=[TextContent(type="text", text="Fetched three notes.")],
        structuredContent={"count": 3},
        isError=False,
    )

    mapped = provider._action_result("notes-server", "fetch_notes", result)

    assert mapped.status == OutcomeStatus.SUCCESS
    assert mapped.summary == "Fetched three notes."
    assert mapped.raw["server_id"] == "notes-server"
    assert mapped.raw["tool_name"] == "fetch_notes"
    assert mapped.raw["structured_content"] == {"count": 3}


def test_search_web_tool_falls_back_to_html_results_when_instant_answer_body_is_empty() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.duckduckgo.com":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/x-javascript"},
                text="",
            )
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text=(
                    '<div class="result results_links results_links_deep web-result">'
                    '<h2 class="result__title">'
                    '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopenai.com%2F">'
                    "Official site"
                    "</a>"
                    "</h2>"
                    '<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopenai.com%2F">'
                    "OpenAI builds useful AI systems."
                    "</a>"
                    "</div>"
                ),
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            result = await SearchWebTool(http_client=client).execute({"query": "OpenAI"})
        assert result.status == OutcomeStatus.SUCCESS
        assert result.raw["provider"] == "duckduckgo_html"
        assert result.raw["results"][0]["url"] == "https://openai.com/"
        assert "Official site" in result.raw["results"][0]["text"]
        assert result.raw["fallback_reason"]["error_type"] == "ValueError"

    import asyncio

    asyncio.run(run())


def test_read_url_tool_retries_with_insecure_tls_after_certificate_failure(monkeypatch) -> None:
    class _FakeAsyncClient:
        def __init__(self, *, response: httpx.Response | None = None, error: Exception | None = None) -> None:
            self._response = response
            self._error = error

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, **kwargs) -> httpx.Response:
            del kwargs
            if self._error is not None:
                raise self._error
            assert self._response is not None
            return self._response

    request = httpx.Request("GET", "https://example.com")
    clients = iter(
        [
            _FakeAsyncClient(
                error=httpx.ConnectError(
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
                    request=request,
                )
            ),
            _FakeAsyncClient(
                response=httpx.Response(
                    200,
                    request=request,
                    headers={"content-type": "text/html"},
                    text="<html><title>Example Domain</title><body>Example body</body></html>",
                )
            ),
        ]
    )

    monkeypatch.setattr(
        "app.tool.internal_provider.httpx.AsyncClient",
        lambda **kwargs: next(clients),
    )

    async def run() -> None:
        result = await ReadUrlTool().execute({"url": "https://example.com"})
        assert result.status == OutcomeStatus.SUCCESS
        assert result.raw["title"] == "Example Domain"
        assert result.raw["transport"]["ssl_verification"] == "disabled_after_cert_failure"

    import asyncio

    asyncio.run(run())
