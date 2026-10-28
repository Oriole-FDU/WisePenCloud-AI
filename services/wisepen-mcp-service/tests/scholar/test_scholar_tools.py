import asyncio
from types import SimpleNamespace

import pytest
from common.core.exceptions import RpcError
from mcp.shared.memory import create_connected_server_and_client_session
from wisepen_mcp.capabilities import build_mcp_server
from wisepen_mcp.service_client import ScholarClient


class RpcSpy:
    def __init__(self):
        self.calls = []
        self.response = {"papers": [{"title": "A local paper"}]}
        self.error = None

    async def post(self, service_name, path, **kwargs):
        self.calls.append((service_name, path, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


class SlowRpcSpy:
    def __init__(self):
        self.cancelled_calls = 0
        self.timeouts = []

    async def post(self, service_name, path, **kwargs):
        self.timeouts.append(kwargs["timeout"])
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            raise


@pytest.fixture
def scholar_server():
    rpc = RpcSpy()
    mcp = build_mcp_server(
        ai_asset_client=SimpleNamespace(),
        note_collab_client=SimpleNamespace(),
        resource_client=SimpleNamespace(),
        document_client=SimpleNamespace(),
        note_client=SimpleNamespace(),
        scholar_client=ScholarClient(rpc),
    )
    return mcp, rpc


@pytest.mark.asyncio
async def test_mcp_scholar_schemas_expose_declared_contracts(scholar_server):
    mcp, _ = scholar_server
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    search = tools["search_papers"].inputSchema
    assert set(search["properties"]) == {
        "query",
        "field",
        "published_after",
        "published_before",
        "limit",
        "source_scope",
    }
    assert search["required"] == ["query"]
    assert search["properties"]["query"]["minLength"] == 1
    assert search["properties"]["field"]["default"] == "general"
    assert search["$defs"]["SearchField"]["enum"] == [
        "general",
        "computer_science",
        "medicine",
        "biology",
        "engineering",
        "economics_business",
        "mathematics",
        "physics",
        "chemistry",
        "psychology",
        "social_sciences",
        "arts_humanities",
        "law",
        "materials_environmental_sciences",
    ]
    assert {"type": "string", "format": "date"} in search["properties"][
        "published_after"
    ]["anyOf"]
    assert {"type": "string", "format": "date"} in search["properties"][
        "published_before"
    ]["anyOf"]
    assert search["properties"]["limit"]["minimum"] == 1
    assert search["properties"]["limit"]["maximum"] == 20
    assert search["properties"]["source_scope"]["default"] == "default"
    assert search["$defs"]["SearchSourceScope"]["enum"] == [
        "default",
        "multi_source",
    ]

    lookup = tools["lookup_papers"].inputSchema
    assert set(lookup["properties"]) == {"identifiers", "field"}
    assert lookup["required"] == ["identifiers"]
    assert lookup["properties"]["identifiers"]["minItems"] == 1
    assert lookup["properties"]["identifiers"]["maxItems"] == 20

    discovery = tools["discover_papers"].inputSchema
    assert set(discovery["properties"]) == {"field", "period", "sort", "limit"}
    assert discovery["required"] == ["field"]
    assert discovery["$defs"]["DiscoveryField"]["enum"] == ["computer_science"]
    assert discovery["$defs"]["DiscoveryPeriod"]["enum"] == [
        "daily",
        "weekly",
        "monthly",
    ]
    assert discovery["$defs"]["DiscoverySort"]["enum"] == ["recent", "trending"]
    assert discovery["properties"]["period"]["default"] == "daily"
    assert discovery["properties"]["sort"]["default"] == "trending"
    assert discovery["properties"]["limit"]["default"] == 10
    assert discovery["properties"]["limit"]["minimum"] == 1
    assert discovery["properties"]["limit"]["maximum"] == 15


@pytest.mark.asyncio
async def test_search_papers_call_passes_http_contract_and_returns_scholar_data(
    scholar_server,
):
    mcp, rpc = scholar_server
    response = {"items": [{"id": "arxiv:2601.12345", "signals": {"citations": 2}}]}
    rpc.response = response

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            "search_papers",
            {
                "query": "  retrieval systems  ",
                "field": "mathematics",
                "published_after": "2026-01-02",
                "published_before": "2026-03-04",
                "limit": 7,
                "source_scope": "default",
            },
        )

    assert result.structuredContent == response
    assert rpc.calls == [
        (
            "wisepen-scholar-service",
            "/scholar/papers/search",
            {
                "json": {
                    "query": "  retrieval systems  ",
                    "field": "mathematics",
                    "published_after": "2026-01-02",
                    "published_before": "2026-03-04",
                    "limit": 7,
                    "source_scope": "default",
                },
                "timeout": 30.0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_lookup_papers_passes_identifiers_and_field(scholar_server):
    mcp, rpc = scholar_server
    response = {"items": [{"id": "doi:10.1000/example"}]}
    rpc.response = response

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            "lookup_papers",
            {"identifiers": [" DOI:10.1000/example "], "field": "medicine"},
        )

    assert result.structuredContent == response
    assert rpc.calls == [
        (
            "wisepen-scholar-service",
            "/scholar/papers/lookup",
            {
                "json": {
                    "identifiers": [" DOI:10.1000/example "],

                    "field": "medicine",
                },
                "timeout": 30.0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_discover_papers_maps_period_and_returns_scholar_data(scholar_server):
    mcp, rpc = scholar_server
    response = {"papers": [{"title": "A discovered paper"}], "window_start": None}
    rpc.response = response

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            "discover_papers",
            {
                "field": "computer_science",
                "period": "weekly",
                "sort": "recent",
                "limit": 6,
            },
        )

    assert result.structuredContent == response
    assert rpc.calls == [
        (
            "wisepen-scholar-service",
            "/scholar/papers/discover",
            {
                "json": {
                    "field": "computer_science",
                    "period": "weekly",
                    "sort": "recent",
                    "limit": 6,
                },
                "timeout": 30.0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_scholar_total_timeout_cancels_slow_calls():
    rpc = SlowRpcSpy()
    client = ScholarClient(rpc, timeout=0.01)

    with pytest.raises(TimeoutError):
        await client.search_papers(
            query="transformers",
            field="general",
            published_after=None,
            published_before=None,
            limit=10,
            source_scope="default",
        )
    with pytest.raises(TimeoutError):
        await client.lookup_papers(identifiers=["arxiv:2401.00001"])
    with pytest.raises(TimeoutError):
        await client.discover_papers(
            field="computer_science",
            period="daily",
            sort="trending",
            limit=10,
        )
    assert rpc.cancelled_calls == 3
    assert rpc.timeouts == [0.01, 0.01, 0.01]


@pytest.mark.asyncio
async def test_byok_metadata_is_per_call_and_only_forwarded_in_http_headers(
    scholar_server,
):
    mcp, rpc = scholar_server
    async with create_connected_server_and_client_session(mcp) as session:
        await session.call_tool(
            "search_papers",
            {"query": "test"},
            meta={
                "com.wisepen/tool_config": {
                    "api_key": "search-user-key",
                    "huggingface_api_key": "hf-search-user",
                }
            },
        )
        await session.call_tool(
            "lookup_papers",
            {"identifiers": ["1706.03762"]},
            meta={
                "com.wisepen/tool_config": {
                    "api_key": "lookup-user-key",
                    "huggingface_api_key": "hf-lookup-user",
                }
            },
        )
        await session.call_tool("search_papers", {"query": "test"})
        await session.call_tool(
            "discover_papers",
            {"field": "computer_science"},
            meta={
                "com.wisepen/tool_config": {"huggingface_api_key": "hf-discover-user"}
            },
        )

    assert rpc.calls[0][2]["headers"] == {
        "X-OpenAlex-Api-Key": "search-user-key",
        "X-HuggingFace-Api-Key": "hf-search-user",
    }
    assert rpc.calls[1][2]["headers"] == {
        "X-OpenAlex-Api-Key": "lookup-user-key",
        "X-HuggingFace-Api-Key": "hf-lookup-user",
    }
    assert "headers" not in rpc.calls[2][2]
    assert rpc.calls[3][2]["headers"] == {"X-HuggingFace-Api-Key": "hf-discover-user"}
    assert all("api_key" not in call[2]["json"] for call in rpc.calls)


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (
            200,
            503,
            "OpenAlex search unavailable: upstream single-source failure",
        ),
        (429, 429, "Too Many Requests: retry after 12 seconds"),
        (None, None, "ReadTimeout: Scholar request exceeded its time budget"),
    ],
)
@pytest.mark.asyncio
async def test_scholar_rpc_failures_are_visible_as_mcp_tool_errors(
    scholar_server, status, code, message
):
    mcp, rpc = scholar_server
    rpc.error = RpcError(
        service_name="wisepen-scholar-service",
        path="/scholar/papers/search",
        status=status,
        code=code,
        msg=message,
    )

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool("search_papers", {"query": "retrieval"})

    visible_error = "\n".join(
        item.text for item in result.content if item.type == "text"
    )
    assert result.isError is True
    assert message in visible_error
    assert "source_error" not in visible_error
