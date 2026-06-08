from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.models import WikipediaGroundingResult
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.results import is_valid_result
from common.logger import log_fail


class WikipediaRunner:
    """Wikipedia Grounding 特异性并行执行器

    - 专职负责 Wikipedia Grounding Keywords 的多路并发平推与限流调度。
    - 无状态设计：内部不持有缓存防线，不管理 httpx.AsyncClient 生命周期。
    - 物理网络与数据清洗已完全托管至底层的 WikipediaSearcher，本类仅聚焦于多路异常隔离与实体映射。
    """

    def __init__(
        self,
        *,
        searcher: WebSearcher,
        max_results_per_keyword: int = 1,
        keyword_concurrency: int = 3,
    ) -> None:

        self._searcher = searcher
        self._max_results_per_keyword = max_results_per_keyword
        self._semaphore = asyncio.Semaphore(keyword_concurrency)

    async def run_keywords(
        self,
        *,
        search_call_id: str,
        keywords: Sequence[str],
    ) -> List[WikipediaGroundingResult]:
        """统一多关键词维基锚定并发调度入口"""
        if not keywords:
            return []

        raw_results = await asyncio.gather(
            *(
                self._run_one_keyword(
                    keyword,
                    search_call_id=search_call_id,
                )
                for keyword in keywords
            ),
            return_exceptions=True,
        )

        return [
            item
            for item in raw_results
            if isinstance(item, WikipediaGroundingResult)
        ]

    async def _run_one_keyword(
        self,
        keyword: str,
        *,
        search_call_id: str,
    ) -> Optional[WikipediaGroundingResult]:
        """单条维基词条知识打捞的内聚生命周期控制"""
        query = keyword.strip() if keyword.strip() else ""
        if not query:
            return None

        async with self._semaphore:
            try:
                response = await self._searcher.search(
                    query,
                    max_results=self._max_results_per_keyword,
                )
            except Exception as e:
                log_fail(
                    f"{SearcherName.WIKIPEDIA.value} variant 搜索",
                    repr(e),
                    search_call_id=search_call_id,
                    query=query,
                )
                return None

        result = self._select_grounding_result(response=response)
        if result is None:
            return None

        # 映射标准落地实体
        return WikipediaGroundingResult(
            keyword=keyword,
            title=result.title,
            extract=result.snippet,
            url=result.url,
            cache_hit=False,
        )

    def _select_grounding_result(
        self,
        *,
        response: SearchResponse,
    ) -> Optional[SearchResult]:
        """从 Wikipedia SearchResponse 中洗出最优的单条有效 grounding 结果"""
        if not response.results:
            return None

        # 严格执行合规性检查断言
        for result in response.results:
            if isinstance(result, SearchResult) and is_valid_result(result):
                return result

        return None
