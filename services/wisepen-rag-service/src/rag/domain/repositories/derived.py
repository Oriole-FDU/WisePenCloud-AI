from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence


class KnowledgeGraphDerivedRepository(ABC):
    """知识抽取候选图的持久化派生结果仓储。"""

    @abstractmethod
    async def get_many(self, keys: Sequence[str]) -> Mapping[str, str]:
        """批量读取派生项；未命中条目不会出现在返回结果中。"""
        pass

    @abstractmethod
    async def set_many(
        self,
        *,
        resource_id: str,
        values: Mapping[str, str],
    ) -> None:
        """批量写入派生项；调用方应保证幂等。"""
        pass


class RagContextIndexingRepository(ABC):
    """chunk 上下文补全结果的资源内持久派生文本仓储。"""

    @abstractmethod
    async def get_many(
        self,
        *,
        resource_id: str,
        keys: Sequence[str],
    ) -> Mapping[str, str]:
        """批量读取派生项；未命中条目不会出现在返回结果中。"""
        pass

    @abstractmethod
    async def set_many(
        self,
        *,
        resource_id: str,
        values: Mapping[str, str],
    ) -> None:
        """批量写入派生项；调用方应保证幂等。"""
        pass
