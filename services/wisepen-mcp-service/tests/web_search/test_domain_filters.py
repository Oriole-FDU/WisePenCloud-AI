"""验证域名意图、按模式调度，以及真实 SDK 发出的 HTTP 请求。"""

import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from common.core.exceptions import ServiceException
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from wisepen_mcp.capabilities.web_search.providers import (
    AnySearchTool,
    BaiduQianfanSearchTool,
    ExaSearchTool,
    FirecrawlSearchTool,
    PlatformSearchTool,
    TavilySearchTool,
    TinyFishSearchTool,
    baidu_qianfan,
    platform_default,
)
from wisepen_mcp.capabilities.web_search.search_tools import (
    ProviderSearchRequest,
    SearchMode,
    SearchRecency,
)
from wisepen_mcp.domain.error_codes import McpErrorCode


@pytest_asyncio.fixture
async def network(monkeypatch):
    # 只替换 HTTP 传输，SDK 的参数校验、序列化和响应解析均使用实际实现。
    state = SimpleNamespace(
        requests=[],
        status=200,
        body={
            "results": [], "answer": None, "success": True,
            "data": {"web": [], "results": []}, "references": [],
        },
    )
    clients = []
    original_init = httpx.AsyncClient.__init__

    def respond(request):
        state.requests.append(request)
        return httpx.Response(state.status, json=state.body)

    def initialize(client, *args, **kwargs):
        kwargs.update(transport=httpx.MockTransport(respond), mounts=None, trust_env=False)
        original_init(client, *args, **kwargs)
        clients.append(client)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", initialize)
    state.client = httpx.AsyncClient()
    yield state
    for client in clients:
        await client.aclose()


@pytest.fixture
def tools(network):
    return {
        "deepseek": PlatformSearchTool(http_client=network.client),
        "exa": ExaSearchTool(),
        "tavily": TavilySearchTool(),
        "tinyfish": TinyFishSearchTool(http_client=network.client),
        "baidu": BaiduQianfanSearchTool(http_client=network.client),
        "firecrawl": FirecrawlSearchTool(),
        "anysearch": AnySearchTool(http_client=network.client),
    }


@pytest.fixture
def ctx():
    return SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(
        model_extra={"com.wisepen/tool_config": {"api_key": "test-provider-key"}},
    )))


@pytest.fixture
def frozen_clock(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz):
            return cls(2026, 10, 17, 0, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(platform_default, "datetime", FrozenDateTime)
    monkeypatch.setattr(baidu_qianfan, "datetime", FrozenDateTime)


@pytest.mark.parametrize("field", ["include_domains", "exclude_domains"])
def test_request_normalizes_hostnames(field):
    request = ProviderSearchRequest(query="Python", **{field: [" Python.ORG ", " Docs.Python.Org "]})
    assert getattr(request, field) == ["python.org", "docs.python.org"]


@pytest.mark.parametrize("field", ["include_domains", "exclude_domains"])
@pytest.mark.parametrize("domain", [
    "", "   ", "https://python.org", "python.org/docs", "python.org:443",
    "*.python.org", "python.org OR github.com", "python.org?query=x",
    "python.org#fragment", "user@python.org", "python..org", "-python.org",
    "python.org\nsite:github.com",
])
def test_request_rejects_non_hostname_filters(field, domain):
    with pytest.raises(ValidationError):
        ProviderSearchRequest(query="Python", **{field: [domain]})


@pytest.mark.parametrize(("name", "expected"), [
    ("deepseek", {"query", "max_results", "recency", "include_domains", "exclude_domains"}),
    ("exa", {"query", "mode", "focus", "recency", "max_results", "include_domains", "exclude_domains"}),
    ("tavily", {"query", "recency", "max_results", "include_domains", "exclude_domains"}),
    ("tinyfish", {"query", "mode", "focus", "max_results", "include_domains", "exclude_domains"}),
    ("baidu", {"query", "recency", "max_results", "include_domains"}),
    ("firecrawl", {"query", "mode", "recency", "max_results", "include_domains", "exclude_domains"}),
    ("anysearch", {"query", "mode", "max_results"}),
])
@pytest.mark.asyncio
async def test_schema_exposes_only_declared_capabilities(tools, name, expected):
    mcp = FastMCP("domain-schema-test")
    tools[name].register(mcp)
    [tool] = await mcp.list_tools()
    assert set(tool.inputSchema["properties"]) == expected
    # 模型看到的是描述与完整 Schema；隐藏参数不能残留在正文或嵌套定义中。
    prompt = tool.description + json.dumps(tool.inputSchema)
    for parameter in ["mode", "focus", "recency", "include_domains", "exclude_domains", "api_key", "ctx"]:
        if parameter not in expected:
            assert re.search(rf"\b{parameter}\b", prompt, re.IGNORECASE) is None
    if "focus" not in expected:
        assert "### Focused Extraction" not in tool.description
    if "recency" not in expected:
        assert "### Recency" not in tool.description
        assert "SearchRecency" not in tool.inputSchema.get("$defs", {})
    if "mode" not in expected:
        assert "### Academic Search" not in tool.description
        assert "SearchMode" not in tool.inputSchema.get("$defs", {})
    if name == "firecrawl":
        assert "only with mode='web'" in tool.description
        assert "mutually exclusive" in tool.description


@pytest.mark.parametrize(("name", "mode", "field"), [
    ("firecrawl", SearchMode.ACADEMIC, "include_domains"),
    ("firecrawl", SearchMode.ACADEMIC, "exclude_domains"),
    ("baidu", SearchMode.WEB, "exclude_domains"),
    ("anysearch", SearchMode.WEB, "include_domains"),
    ("anysearch", SearchMode.ACADEMIC, "exclude_domains"),
])
@pytest.mark.asyncio
async def test_unsupported_filter_is_rejected_without_request(tools, network, ctx, name, mode, field):
    with pytest.raises(ServiceException, match=f"does not support {field}") as exc:
        await tools[name].execute(ctx=ctx, query="Python", mode=mode, **{field: ["python.org"]})
    assert exc.value.code == McpErrorCode.WEB_SEARCH_INVALID.code
    assert network.requests == []


@pytest.mark.asyncio
async def test_execute_passes_normalized_domains_and_preserves_query(tools, network, ctx):
    network.body = {"results": [{"title": "Python", "url": "https://python.org", "summary": "Python docs."}]}
    result = await tools["deepseek"].execute(
        ctx=ctx, query="  Python site:example.com  ",
        include_domains=[" Python.ORG "], exclude_domains=[" Docs.Python.Org "],
    )
    assert json.loads(network.requests[0].content)["query"] == (
        "Python site:example.com site:python.org -site:docs.python.org"
    )
    assert result.query == "Python site:example.com"


@pytest.mark.parametrize(("name", "mode"), [
    ("exa", SearchMode.WEB), ("exa", SearchMode.ACADEMIC),
    ("tavily", SearchMode.WEB),
    ("tinyfish", SearchMode.WEB), ("tinyfish", SearchMode.ACADEMIC),
])
@pytest.mark.asyncio
async def test_real_sdk_and_http_requests_carry_combined_filters(tools, network, name, mode):
    handler = tools[name].search_academic if mode is SearchMode.ACADEMIC else tools[name].search_web
    await handler(
        query="Python", max_results=3, api_key="test-provider-key",
        include_domains=["python.org", "github.com"], exclude_domains=["docs.python.org"],
    )
    [request] = network.requests
    if name == "tinyfish":
        assert request.method == "GET"
        assert request.url.params["include_domains"] == "python.org,github.com"
        assert request.url.params["exclude_domains"] == "docs.python.org"
        assert request.url.params.get("domain_type") == ("research_paper" if mode is SearchMode.ACADEMIC else None)
    else:
        body = json.loads(request.content)
        include_key = "includeDomains" if name == "exa" else "include_domains"
        exclude_key = "excludeDomains" if name == "exa" else "exclude_domains"
        assert body[include_key] == ["python.org", "github.com"]
        assert body[exclude_key] == ["docs.python.org"]
        if name == "exa":
            assert body.get("category") == ("research paper" if mode is SearchMode.ACADEMIC else None)
            assert body["contents"]["text"] is False
        else:
            assert body.get("include_domains_mode") is None
            assert body["include_raw_content"] is False


@pytest.mark.parametrize("field", ["include_domains", "exclude_domains"])
@pytest.mark.asyncio
async def test_firecrawl_sdk_serializes_web_filters(tools, network, field):
    await tools["firecrawl"].search_web(
        query="Python", max_results=3, api_key="test-provider-key", **{field: ["python.org"]},
    )
    [request] = network.requests
    body = json.loads(request.content)
    assert request.url.path == "/v2/search"
    assert body["includeDomains" if field == "include_domains" else "excludeDomains"] == ["python.org"]
    assert "scrapeOptions" not in body


@pytest.mark.asyncio
async def test_firecrawl_rejects_combined_filters_before_network(tools, network):
    with pytest.raises(ServiceException, match="mutually exclusive") as exc:
        await tools["firecrawl"].search_web(
            query="Python", max_results=3, api_key="test-provider-key",
            include_domains=["python.org"], exclude_domains=["docs.python.org"],
        )
    assert exc.value.code == McpErrorCode.WEB_SEARCH_INVALID.code
    assert network.requests == []


@pytest.mark.parametrize("recency", [None, SearchRecency.DAY, SearchRecency.WEEK])
@pytest.mark.asyncio
async def test_baidu_sites_merge_with_date_filter(tools, network, frozen_clock, recency):
    await tools["baidu"].search_web(
        query="Python", max_results=3, api_key="test-provider-key", recency=recency,
        include_domains=["python.org", "github.com"],
    )
    body = json.loads(network.requests[0].content)
    assert body["search_filter"]["match"]["site"] == ["python.org", "github.com"]
    if recency is SearchRecency.DAY:
        assert body["search_filter"]["range"] == {"page_time": {"gte": "2026-10-17", "lte": "2026-10-17"}}
    elif recency is SearchRecency.WEEK:
        assert body["search_recency_filter"] == "week"


@pytest.mark.asyncio
async def test_deepseek_combines_flat_domain_and_date_syntax(tools, network, frozen_clock):
    await tools["deepseek"].search_web(
        query='Python "TaskGroup"', max_results=3, api_key=None, recency=SearchRecency.WEEK,
        include_domains=["python.org", "github.com"], exclude_domains=["docs.python.org"],
    )
    assert json.loads(network.requests[0].content) == {
        "query": 'Python "TaskGroup" site:python.org OR site:github.com -site:docs.python.org after:2026-10-10',
        "count": 3, "need_content": False,
    }


@pytest.mark.parametrize("final_length", [128, 129])
@pytest.mark.asyncio
async def test_deepseek_length_includes_domain_and_date_syntax(tools, network, frozen_clock, final_length):
    suffix = " site:python.org -site:docs.python.org after:2026-10-16"
    kwargs = {
        "query": "搜" * (final_length - len(suffix)), "max_results": 1, "api_key": None,
        "include_domains": ["python.org"], "exclude_domains": ["docs.python.org"], "recency": SearchRecency.DAY,
    }
    if final_length == 129:
        with pytest.raises(ServiceException, match="reduce the domains") as exc:
            await tools["deepseek"].search_web(**kwargs)
        assert exc.value.code == McpErrorCode.WEB_SEARCH_INVALID.code
        assert network.requests == []
    else:
        await tools["deepseek"].search_web(**kwargs)
        assert json.loads(network.requests[0].content)["query"] == kwargs["query"] + suffix


@pytest.mark.parametrize("domains", [None, []])
@pytest.mark.asyncio
async def test_empty_filters_are_omitted_from_native_request(tools, network, domains):
    await tools["tinyfish"].search_web(
        query="Python", max_results=3, api_key="test-provider-key", include_domains=domains, exclude_domains=domains,
    )
    assert dict(network.requests[0].url.params) == {"query": "Python"}


@pytest.mark.asyncio
async def test_empty_unsupported_filters_do_not_block_academic_search(tools, network, ctx):
    with pytest.raises(ServiceException) as exc:
        await tools["anysearch"].execute(
            ctx=ctx, query="Python", mode=SearchMode.ACADEMIC, include_domains=[], exclude_domains=[],
        )
    assert exc.value.code == McpErrorCode.WEB_SEARCH_EMPTY_RESULT.code
    [request] = network.requests
    body = json.loads(request.content)
    assert body["tag"] == "academic.search"
    assert "include_domains" not in body and "exclude_domains" not in body
