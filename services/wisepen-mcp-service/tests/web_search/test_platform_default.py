import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from common.core.exceptions import ServiceException
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.web_search.providers import platform_default
from wisepen_mcp.capabilities.web_search.providers.platform_default import (
    PlatformSearchTool,
)
from wisepen_mcp.capabilities.web_search.search_tools import SearchMode, SearchRecency
from wisepen_mcp.domain.error_codes import McpErrorCode


@pytest.fixture
def result_item():
    return {
        "title": "Search documentation",
        "url": "https://example.com/search",
        "date": "2026-10-16T00:00:00+00:00",
        "summary": " Model-oriented summary. ",
        "snippet": "Display snippet.",
        "content": "FULL_PAGE_CONTENT_MUST_NOT_ESCAPE",
        "rerank_score": 0.0,
        "authority": -1,
        "site_name": " Example Docs ",
        "site_icon": "https://example.com/icon.png",
    }


@pytest.fixture
def upstream(result_item):
    return SimpleNamespace(
        response=httpx.Response(200, json={"results": [result_item]}),
        requests=[],
    )


@pytest_asyncio.fixture
async def tool(upstream):
    def respond(request):
        upstream.requests.append(request)
        if isinstance(upstream.response, Exception):
            raise upstream.response
        return upstream.response

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        yield PlatformSearchTool(http_client=client)


@pytest.fixture
def ctx():
    return SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(
                model_extra={"com.wisepen/tool_config": {"api_key": "caller-key"}},
            ),
        ),
    )


@pytest.fixture
def frozen_clock(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return cls(2026, 10, 17, 0, 30, tzinfo=tz)

    monkeypatch.setattr(platform_default, "datetime", FrozenDateTime)


@pytest.mark.asyncio
async def test_execute_preserves_public_contract_and_excludes_full_content(tool, upstream, ctx):
    result = await tool.execute(ctx=ctx, query="  DeepSeek site:example.com  ", max_results=3)

    assert len(upstream.requests) == 1
    request = upstream.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
    assert request.headers["Authorization"] == "Bearer test-platform-key"
    assert json.loads(request.content) == {
        "query": "DeepSeek site:example.com",
        "count": 3,
        "need_content": False,
    }
    assert result.query == "DeepSeek site:example.com"
    assert result.mode is SearchMode.WEB
    assert result.summary is None
    assert result.candidates[0].model_dump() == {
        "title": "Search documentation",
        "url": "https://example.com/search",
        "published_date": "2026-10-16T00:00:00+00:00",
        "evidences": ["Model-oriented summary."],
        "metadata": {"rerank_score": 0.0, "authority": -1, "site_name": "Example Docs"},
    }
    assert "FULL_PAGE_CONTENT_MUST_NOT_ESCAPE" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_base_url_accepts_trailing_slash(tool, upstream, monkeypatch):
    monkeypatch.setattr(platform_default.settings, "WEB_SEARCH_DEEPSEEK_BASE_URL", "https://search.example.com/")

    await tool.search_web(query="DeepSeek", max_results=1, api_key=None)

    assert str(upstream.requests[0].url) == "https://search.example.com/v1/tools/search"


@pytest.mark.parametrize(
    ("recency", "after"),
    [
        (SearchRecency.DAY, "2026-10-16"),
        (SearchRecency.WEEK, "2026-10-10"),
        (SearchRecency.MONTH, "2026-09-17"),
        (SearchRecency.YEAR, "2025-10-17"),
    ],
)
@pytest.mark.asyncio
async def test_recency_uses_utc_dates_and_keeps_original_query(tool, upstream, ctx, frozen_clock, recency, after):
    query = 'DeepSeek "Search API" site:example.com lang:en before:2026-10-18'

    result = await tool.execute(ctx=ctx, query=query, recency=recency)

    assert json.loads(upstream.requests[0].content)["query"] == f"{query} after:{after}"
    assert result.query == query


@pytest.mark.parametrize("recency", [None, SearchRecency.DAY])
@pytest.mark.parametrize("final_length", [128, 129])
@pytest.mark.asyncio
async def test_query_length_counts_unicode_characters_and_recency(tool, upstream, frozen_clock, recency, final_length):
    suffix = " after:2026-10-16" if recency is not None else ""
    query = "搜" * (final_length - len(suffix))

    if final_length == 129:
        with pytest.raises(ServiceException, match="Shorten the query") as exc:
            await tool.search_web(query=query, max_results=1, api_key=None, recency=recency)
        assert exc.value.code == McpErrorCode.WEB_SEARCH_INVALID.code
        assert upstream.requests == []
    else:
        await tool.search_web(query=query, max_results=1, api_key=None, recency=recency)
        assert json.loads(upstream.requests[0].content)["query"] == query + suffix


@pytest.mark.parametrize("summary", [None, "", "   "])
@pytest.mark.asyncio
async def test_summary_falls_back_to_snippet(tool, upstream, result_item, summary):
    result_item.update(summary=summary, date=None, site_name="   ", authority=0)
    upstream.response = httpx.Response(200, json={"results": [result_item]})

    result = await tool.search_web(query="DeepSeek", max_results=1, api_key=None)

    assert result.results[0].evidences == ["Display snippet."]
    assert result.results[0].published_date is None
    assert result.results[0].metadata == {"rerank_score": 0.0, "authority": 0}


@pytest.mark.asyncio
async def test_missing_evidence_does_not_use_full_content(tool, upstream, result_item):
    result_item.pop("summary")
    result_item.pop("snippet")
    upstream.response = httpx.Response(200, json={"results": [result_item]})

    result = await tool.search_web(query="DeepSeek", max_results=1, api_key=None)

    assert result.results[0].evidences == []


@pytest.mark.asyncio
async def test_base_keeps_provider_order_deduplicates_and_limits_results(tool, upstream, result_item, ctx):
    upstream.response = httpx.Response(200, json={
        "results": [
            result_item,
            {**result_item, "title": "Duplicate"},
            {**result_item, "url": "https://example.com/second", "title": "Second", "rerank_score": 2.5},
            {**result_item, "url": "https://example.com/third", "title": "Third"},
        ],
    })

    result = await tool.execute(ctx=ctx, query="DeepSeek", max_results=2)

    assert [item.title for item in result.candidates] == ["Search documentation", "Second"]


@pytest.mark.asyncio
async def test_empty_results_use_existing_base_error(tool, upstream, ctx):
    upstream.response = httpx.Response(200, json={"results": []})

    with pytest.raises(ServiceException) as exc:
        await tool.execute(ctx=ctx, query="DeepSeek")

    assert exc.value.code == McpErrorCode.WEB_SEARCH_EMPTY_RESULT.code


@pytest.mark.asyncio
async def test_missing_platform_key_rejects_caller_key_before_request(tool, upstream, ctx, monkeypatch):
    monkeypatch.setattr(platform_default.settings, "WEB_SEARCH_API_KEY", "   ")

    with pytest.raises(ServiceException) as exc:
        await tool.execute(ctx=ctx, query="DeepSeek")

    assert exc.value.code == McpErrorCode.WEB_SEARCH_CONFIG_MISSING.code
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, McpErrorCode.WEB_SEARCH_INVALID),
        (401, McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID),
        (402, McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID),
        (403, McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID),
        (408, McpErrorCode.WEB_SEARCH_UNAVAILABLE),
        (429, McpErrorCode.WEB_SEARCH_UNAVAILABLE),
        (500, McpErrorCode.WEB_SEARCH_UNAVAILABLE),
        (504, McpErrorCode.WEB_SEARCH_UNAVAILABLE),
        (404, McpErrorCode.WEB_SEARCH_FAILED),
        (413, McpErrorCode.WEB_SEARCH_FAILED),
    ],
)
@pytest.mark.asyncio
async def test_http_errors_keep_cause_and_do_not_retry(tool, upstream, status, code):
    upstream.response = httpx.Response(status, json={"error": {"message": "upstream error"}})

    with pytest.raises(ServiceException) as exc:
        await tool.search_web(query="DeepSeek", max_results=1, api_key=None)

    assert exc.value.code == code.code
    assert isinstance(exc.value.__cause__, httpx.HTTPStatusError)
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
@pytest.mark.asyncio
async def test_network_errors_keep_cause_and_do_not_retry(tool, upstream, error_type):
    error = error_type("simulated network failure")
    upstream.response = error

    with pytest.raises(ServiceException) as exc:
        await tool.search_web(query="DeepSeek", max_results=1, api_key=None)

    assert exc.value.code == McpErrorCode.WEB_SEARCH_UNAVAILABLE.code
    assert exc.value.__cause__ is error
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"results": null}',
        b'{"results": {}}',
        b'{"results": ""}',
        b'{"results": [null]}',
        b'{"results": [{}]}',
        b'{"results": [{"title": "Missing URL"}]}',
        b'{"results": [{"title": "T", "url": "https://example.com", "summary": 7}]}',
    ],
)
@pytest.mark.asyncio
async def test_malformed_response_is_failed_instead_of_empty(tool, upstream, body):
    upstream.response = httpx.Response(200, content=body)

    with pytest.raises(ServiceException) as exc:
        await tool.search_web(query="DeepSeek", max_results=1, api_key=None)

    assert exc.value.code == McpErrorCode.WEB_SEARCH_FAILED.code


@pytest.mark.asyncio
async def test_fastmcp_schema_keeps_default_tool_identity_and_inputs(tool):
    mcp = FastMCP("platform-search-test")
    tool.register(mcp)

    registered = await mcp.list_tools()

    assert len(registered) == 1
    assert registered[0].name == "default_web_search"
    schema = registered[0].inputSchema
    assert set(schema["properties"]) == {
        "query", "max_results", "recency", "include_domains", "exclude_domains",
    }
    assert schema["required"] == ["query"]
    assert schema["properties"]["max_results"]["default"] == 10
    assert schema["properties"]["max_results"]["maximum"] == 20
    assert schema["$defs"]["SearchRecency"]["enum"] == ["day", "week", "month", "year"]
    assert tool.provider_name is None
    assert tool.requires_api_key is False
