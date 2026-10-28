"""顶级业务用例服务。"""

from scholar.application.services.discovery import PaperDiscoveryService
from scholar.application.services.lookup import PaperLookupService
from scholar.application.services.search import PaperSearchService

__all__ = ["PaperDiscoveryService", "PaperLookupService", "PaperSearchService"]
