import httpx
from mcp.server.fastmcp import FastMCP
from zeroentropy import AsyncZeroEntropy

from common.utils.ranking import RankingPipeline
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.rerankers import ZeroEntropyReranker, ZeroEntropyRerankerConfig
from common.utils.ranking.scorers import FieldedBM25Scorer, FieldedBM25ScorerConfig
from common.utils.ranking.tokenizer import ThuLacRankingTokenizer
from .providers import (
    AnySearchTool,
    BaiduQianfanSearchTool,
    ExaSearchTool,
    FirecrawlSearchTool,
    PlatformSearchTool,
    TavilySearchTool,
    TinyFishSearchTool,
)
from .search_tools import (
    BaseSearchTool,
    SearchMode,
    WebSearchToolResult,
)
from ...core.config.app_settings import settings


def register_web_search_tools(
    mcp: FastMCP,
) -> None:


    web_search_fielded_bm25_scorer = FieldedBM25Scorer(
        tokenizer=ThuLacRankingTokenizer(),
        config=FieldedBM25ScorerConfig(
            field_weights={
                "title": 3.0,
                "snippet": 1.5,
                "highlights": 1.0,
            },
            min_score=-1.0,
        ),
    )

    web_search_zero_entropy_reranker = ZeroEntropyReranker(
        client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
        config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
    ) if settings.ZERO_ENTROPY_API_KEY else None

    web_search_ranking_pipeline = RankingPipeline(
        scorers=(web_search_fielded_bm25_scorer,),
        fusion=WeightedRrfFusion(),
        reranker=web_search_zero_entropy_reranker,
    )

    web_search_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.WEB_SEARCH_HTTP_TIMEOUT_SECONDS),
    )

    platform_search_tool = PlatformSearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    exa_search_tool = ExaSearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    tavily_search_tool = TavilySearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    anysearch_search_tool = AnySearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    baidu_qianfan_search_tool = BaiduQianfanSearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    tinyfish_search_tool = TinyFishSearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    firecrawl_search_tool = FirecrawlSearchTool(
        http_client=web_search_http_client,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    web_search_tools = [
        platform_search_tool,
        exa_search_tool,
        tavily_search_tool,
        anysearch_search_tool,
        baidu_qianfan_search_tool,
        tinyfish_search_tool,
        firecrawl_search_tool,
    ]

    for tool in web_search_tools:
        tool.register(mcp)


__all__ = ["register_web_search_tools"]
