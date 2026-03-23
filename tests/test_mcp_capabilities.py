import httpx
import pytest

from app.core.outcomes import OutcomeStatus
from app.mcp.builtins import register_builtin_capabilities
from app.mcp.compat import MCPCompatLayer
from app.mcp.registry import CapabilityRegistry
from app.mcp.schemas import ActionRequest


@pytest.mark.anyio
async def test_read_url_capability_extracts_html_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/article"
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Example Article</title></head>"
                "<body><p>Important detail.</p><script>ignored()</script>"
                "<p>Second paragraph.</p></body></html>"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry = register_builtin_capabilities(
        CapabilityRegistry(),
        read_url_http_client=client,
    )
    layer = MCPCompatLayer(registry)

    try:
        result = await layer.execute(
            ActionRequest(
                capability="read_url",
                arguments={"url": "https://example.com/article"},
            )
        )
    finally:
        await client.aclose()

    assert result.status == OutcomeStatus.SUCCESS
    assert result.summary.startswith("Read Example Article:")
    assert result.raw["title"] == "Example Article"
    assert result.raw["content"] == "Important detail. Second paragraph."


@pytest.mark.anyio
async def test_read_url_capability_requires_a_valid_url() -> None:
    registry = register_builtin_capabilities(CapabilityRegistry())
    layer = MCPCompatLayer(registry)

    result = await layer.execute(
        ActionRequest(
            capability="read_url",
            arguments={"url": "not-a-url"},
        )
    )

    assert result.status == OutcomeStatus.BLOCKED_FAILURE
    assert "valid http or https URL" in result.summary


@pytest.mark.anyio
async def test_search_web_capability_returns_structured_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "quantum bananas"
        return httpx.Response(
            status_code=200,
            request=request,
            json={
                "Heading": "Quantum bananas",
                "AbstractText": "A fictional topic about fruit-assisted time travel.",
                "AbstractURL": "https://example.com/quantum-bananas",
                "RelatedTopics": [
                    {
                        "Text": "Quantum banana theory overview",
                        "FirstURL": "https://example.com/overview",
                    },
                    {
                        "Topics": [
                            {
                                "Text": "Banana chronodynamics",
                                "FirstURL": "https://example.com/chronodynamics",
                            }
                        ]
                    },
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry = register_builtin_capabilities(
        CapabilityRegistry(),
        search_web_http_client=client,
    )
    layer = MCPCompatLayer(registry)

    try:
        result = await layer.execute(
            ActionRequest(
                capability="search_web",
                arguments={"query": "quantum bananas"},
            )
        )
    finally:
        await client.aclose()

    assert result.status == OutcomeStatus.SUCCESS
    assert result.summary.startswith("Searched web for Quantum bananas:")
    assert result.raw["provider"] == "duckduckgo_instant_answer"
    assert result.raw["results"][0]["text"] == "Quantum banana theory overview"
