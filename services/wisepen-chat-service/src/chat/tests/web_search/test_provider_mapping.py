from __future__ import annotations

import pytest

from chat.application.tools.search_tools.web_search.services.providers.anysearch import (
    AnySearchSearcher,
)
from chat.application.tools.search_tools.web_search.services.providers.core.models import (
    SearchProviderName,
)
from chat.application.tools.search_tools.web_search.services.providers.exa import (
    ExaSearcher,
)
from chat.application.tools.search_tools.web_search.services.providers.tavily import (
    TavilySearcher,
)


def test_exa_maps_summary_and_highlights() -> None:
    response = ExaSearcher.map_response(
        {
            "results": [
                {
                    "title": "Exa result",
                    "url": "https://example.com/exa",
                    "summary": "Generated summary",
                    "highlights": ["First excerpt", "Second excerpt"],
                }
            ],
        },
        query="exa query",
        max_results=10,
    )

    assert response.provider is SearchProviderName.EXA
    assert response.answer is None
    assert response.results[0].preview.snippet == "Generated summary"
    assert response.results[0].preview.highlights == (
        "First excerpt",
        "Second excerpt",
    )


def test_tavily_maps_content_and_top_level_answer() -> None:
    response = TavilySearcher.map_response(
        {
            "answer": "Provider answer",
            "results": [
                {
                    "title": "Tavily result",
                    "url": "https://example.com/tavily",
                    "content": "Result content",
                }
            ],
        },
        query="tavily query",
        max_results=10,
    )

    assert response.provider is SearchProviderName.TAVILY
    assert response.answer == "Provider answer"
    assert response.results[0].preview.snippet == "Result content"


def test_anysearch_uses_data_results_snippet() -> None:
    response = AnySearchSearcher.map_response(
        {
            "data": {
                "results": [
                    {
                        "title": "AnySearch result",
                        "url": "https://example.com/anysearch",
                        "snippet": "Short result preview",
                        "content": "Long result content",
                    }
                ],
            },
        },
        query="anysearch query",
        max_results=10,
    )

    assert response.provider is SearchProviderName.ANYSEARCH
    assert response.answer is None
    assert response.results[0].preview.snippet == "Short result preview"


def test_anysearch_missing_snippet_fails_response_mapping() -> None:
    with pytest.raises(KeyError, match="snippet"):
        AnySearchSearcher.map_response(
            {
                "data": {
                    "results": [
                        {
                            "title": "AnySearch result",
                            "url": "https://example.com/anysearch",
                        }
                    ],
                },
            },
            query="anysearch query",
            max_results=10,
        )
