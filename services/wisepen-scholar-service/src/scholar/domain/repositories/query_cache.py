"""可丢弃的论文查询缓存；不保存请求执行状态。"""

from typing import Protocol

from scholar.domain.models.paper import Paper


class QueryCacheRepository(Protocol):
    async def get(self, key: str) -> list[Paper] | None: ...
    async def put(self, key: str, papers: list[Paper]) -> None: ...
