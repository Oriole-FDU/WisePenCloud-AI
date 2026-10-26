"""按显式领域执行可用检索计划；领域配置不依据结果内容改变。"""

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum

import orjson
from common.core.exceptions import ServiceException

from scholar.application.source_execution import SourceExecution
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import PaperRepository
from scholar.domain.repositories.query_cache_repository import QueryCacheRepository
from scholar.providers.field_mapping import ARXIV_CATEGORIES, AcademicField


class SourceScope(StrEnum):
    DEFAULT = "default"
    MULTI_SOURCE = "multi_source"


@dataclass
class PaperSearch:
    query: str
    field: AcademicField = AcademicField.GENERAL
    published_after: date | None = None
    published_before: date | None = None
    limit: int = 10
    source_scope: SourceScope = SourceScope.DEFAULT


@dataclass
class SearchResult:
    papers: list[Paper]
    execution: list[SourceExecution]


class PaperSearchService:
    """编排检索计划、缓存与结果；来源适配器拥有具体查询协议。"""

    def __init__(
        self, paper_repository: PaperRepository, query_cache: QueryCacheRepository
    ):
        self.paper_repository, self.query_cache = paper_repository, query_cache

    async def search(self, query: PaperSearch) -> SearchResult:
        categories = ARXIV_CATEGORIES[query.field]
        if query.source_scope != SourceScope.DEFAULT or categories is None:
            raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE)
        # 能力检查先于缓存读取，防止把已失效的执行计划误报为可用。
        key = hashlib.sha256(
            orjson.dumps(asdict(query), option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
        papers = await self.query_cache.get(key)
        hit = papers is not None
        if not hit:
            papers = await self.paper_repository.search(
                query=query.query,
                categories=categories,
                published_after=query.published_after,
                published_before=query.published_before,
                limit=query.limit,
            )
            await self.query_cache.put(key, papers)
        return SearchResult(
            papers=papers,
            execution=[
                SourceExecution(
                    source=PaperSource.ARXIV,
                    cache_hit=hit,
                    coverage="仅覆盖 arXiv 收录；映射领域仅代表对应 arXiv 分类。",
                )
            ],
        )
