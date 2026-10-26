import asyncio
from types import SimpleNamespace

import pytest

from wisepen_mcp.capabilities import build_mcp_server
from wisepen_mcp.service_client import ScholarClient


class RpcSpy:
    def __init__(self):
        self.calls = []
        self.response = {"papers": [{"title": "A local paper"}]}

    async def post(self, service_name, path, **kwargs):
        self.calls.append((service_name, path, kwargs))
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
    assert set(lookup["properties"]) == {"identifiers", "purpose"}
    assert lookup["required"] == ["identifiers"]
    assert lookup["properties"]["identifiers"]["minItems"] == 1
    assert lookup["properties"]["identifiers"]["maxItems"] == 20
    assert lookup["properties"]["purpose"]["default"] == "details"
    assert lookup["$defs"]["LookupPurpose"]["enum"] == [
        "details",
        "enrich",
        "full",
    ]


@pytest.mark.asyncio
async def test_search_papers_call_passes_http_contract_and_returns_scholar_data(
    scholar_server,
):
    mcp, rpc = scholar_server
    response = {"items": [{"id": "arxiv:2601.12345", "signals": {"citations": 2}}]}
    rpc.response = response

    result = await mcp.call_tool(
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

    assert result[1] == response
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
async def test_lookup_papers_call_passes_identifiers_and_p1_purpose(scholar_server):
    mcp, rpc = scholar_server
    response = {"items": [{"id": "doi:10.1000/example"}]}
    rpc.response = response

    result = await mcp.call_tool(
        "lookup_papers",
        {"identifiers": [" DOI:10.1000/example "]},
    )

    assert result[1] == response
    assert rpc.calls == [
        (
            "wisepen-scholar-service",
            "/scholar/papers/lookup",
            {
                "json": {
                    "identifiers": [" DOI:10.1000/example "],
                    "purpose": "details",
                },
                "timeout": 30.0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_scholar_total_timeout_cancels_slow_search_and_lookup_calls():
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
        await client.lookup_papers(identifiers=["arxiv:2401.00001"], purpose="details")

    assert rpc.cancelled_calls == 2
    assert rpc.timeouts == [0.01, 0.01]
