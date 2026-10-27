"""按请求周期复用成功的渠道发现结果。"""

from typing import Protocol

from scholar.domain.interfaces.paper_discoverer import PaperDiscovery


class DiscoveryCacheRepository(Protocol):
    async def get(self, key: str) -> PaperDiscovery | None: ...

    async def put(self, key: str, discovery: PaperDiscovery) -> None: ...
