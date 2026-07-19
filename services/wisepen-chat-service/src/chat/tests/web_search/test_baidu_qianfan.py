from __future__ import annotations

from chat.application.tools.search_tools.web_search.services.providers.baidu_qianfan import (
    BaiduQianfanSearchRequest,
    BaiduQianfanSearcher,
)
from chat.application.tools.search_tools.web_search.services.providers.core.models import (
    SearchProviderName,
)
from chat.application.tools.search_tools.web_search.services.sources import (
    SearchSourceFactory,
    WebSearchSourceScope,
)


def test_baidu_qianfan_request_uses_the_ai_search_web_endpoint() -> None:
    request = BaiduQianfanSearchRequest(
        query="百度千帆 AI 搜索", max_results=3
    ).to_http_request()

    assert request.method == "POST"
    assert request.path == "/v2/ai_search/web_search"
    assert request.json == {
        "messages": [{"role": "user", "content": "百度千帆 AI 搜索"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": 3}],
    }


def test_baidu_qianfan_keeps_web_references_and_deduplicates_urls() -> None:
    response = BaiduQianfanSearcher.map_response(
        {
            "answer": "供应商直答",
            "references": [
                {
                    "type": "web",
                    "title": "千帆 API 文档",
                    "url": "https://example.com/doc",
                    "content": "接口说明",
                },
                {"type": "image", "title": "图片", "url": "https://example.com/image"},
                {
                    "title": "未标类型网页",
                    "url": "https://example.com/page",
                    "snippet": "兼容结果",
                },
                {"type": "web", "title": "重复 URL", "url": "https://example.com/page"},
            ],
        },
        query="千帆搜索",
        max_results=10,
    )

    assert response.provider == SearchProviderName.BAIDU_QIANFAN
    assert response.answer == "供应商直答"
    assert [item.title for item in response.results] == [
        "千帆 API 文档",
        "未标类型网页",
    ]


def test_source_factory_routes_baidu_to_its_own_searcher() -> None:
    factory = SearchSourceFactory(
        http_client=object(),
        platform_default_searcher=object(),
        exa_base_url="https://api.exa.ai",
        tavily_base_url="https://api.tavily.com",
        anysearch_base_url="https://api.anysearch.com",
        baidu_qianfan_base_url="https://qianfan.baidubce.com",
    )

    source = factory.build(
        provider=SearchProviderName.BAIDU_QIANFAN, api_key="qianfan-key"
    )

    assert isinstance(source.searcher, BaiduQianfanSearcher)
    assert source.scope is WebSearchSourceScope.PRIVATE
    assert source.source_id == "custom:baidu_qianfan"

    platform_source = factory.build(provider=None, api_key=None)

    assert platform_source.scope is WebSearchSourceScope.PUBLIC
    assert platform_source.source_id == "platform_default"
