"""检索阶段的协议定义，支持从底层存储配置构造与执行查询召回。"""

from typing import Any, Protocol, TypeVar

from common.utils.retrieval import Candidate

PayloadT = TypeVar("PayloadT")


class Retriever(Protocol[PayloadT]):
    @classmethod
    def from_storage(cls, *args: Any, **kwargs: Any) -> Any:
        """从底层存储配置构造检索器实例。"""
        ...

    async def retrieve(self, query: str) -> list[Candidate[PayloadT]]:
        """执行查询检索，返回候选结果列表。"""
        ...
