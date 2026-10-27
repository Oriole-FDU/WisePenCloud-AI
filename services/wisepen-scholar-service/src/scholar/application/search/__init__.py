"""论文检索用例的唯一公开入口。"""

from scholar.application.search.use_case import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
    SearchResult,
    SourceScope,
)

__all__ = [
    "PaperSearch",
    "PaperSearchService",
    "SearchBinding",
    "SearchResult",
    "SourceScope",
]
