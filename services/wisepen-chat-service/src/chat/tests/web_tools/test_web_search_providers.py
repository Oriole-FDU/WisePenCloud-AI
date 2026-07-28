import json

import httpx
import pytest

from chat.application.tools.search_tools.web_search.services.providers import (
    FirecrawlSearcher,
    TinyFishSearcher,
)
from chat.application.tools.search_tools.web_search.services.providers.base import (
    SearchProviderConfig,
)


@pytest.mark.asyncio
async def test_tinyfish_searches_web_and_academic_papers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Attention Is All You Need",
                        "url": "https://arxiv.org/abs/1706.03762",
                        "snippet": "Transformer architecture.",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        searcher = TinyFishSearcher(
            http_client=http_client,
            config=SearchProviderConfig(
                base_url="https://api.search.tinyfish.ai",
                api_key="tinyfish-key",
            ),
        )
        web_response = await searcher.search_web(
            query="transformers",
            max_results=5,
        )
        academic_response = await searcher.search_academic(
            query="transformers",
            max_results=5,
        )

    assert web_response.results[0].snippet == "Transformer architecture."
    assert academic_response.results[0].url == "https://arxiv.org/abs/1706.03762"
    assert requests[0].headers["X-API-Key"] == "tinyfish-key"
    assert dict(requests[0].url.params) == {"query": "transformers"}
    assert dict(requests[1].url.params) == {
        "query": "transformers",
        "domain_type": "research_paper",
    }


@pytest.mark.asyncio
async def test_firecrawl_searches_web_and_academic_research() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "Attention Is All You Need",
                            "url": "https://arxiv.org/abs/1706.03762",
                            "description": "Transformer architecture.",
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        searcher = FirecrawlSearcher(
            http_client=http_client,
            config=SearchProviderConfig(
                base_url="https://api.firecrawl.dev",
                api_key="firecrawl-key",
            ),
        )
        web_response = await searcher.search_web(
            query="transformers",
            max_results=5,
        )
        academic_response = await searcher.search_academic(
            query="transformers",
            max_results=5,
        )

    assert web_response.results[0].snippet == "Transformer architecture."
    assert academic_response.results[0].url == "https://arxiv.org/abs/1706.03762"
    assert requests[0].headers["Authorization"] == "Bearer firecrawl-key"
    assert json.loads(requests[0].content) == {
        "query": "transformers",
        "limit": 5,
        "sources": ["web"],
    }
    assert json.loads(requests[1].content) == {
        "query": "transformers",
        "limit": 5,
        "sources": ["web"],
        "categories": ["research"],
    }
