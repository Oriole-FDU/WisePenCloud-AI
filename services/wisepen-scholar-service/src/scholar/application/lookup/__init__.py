"""论文精确查询用例；purpose 控制本用例内的详情和富化行为。"""

from scholar.application.lookup.use_case import (
    LookupItem,
    LookupPurpose,
    LookupResult,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)

__all__ = [
    "LookupItem",
    "LookupPurpose",
    "LookupResult",
    "LookupStatus",
    "PaperLookup",
    "PaperLookupService",
]
