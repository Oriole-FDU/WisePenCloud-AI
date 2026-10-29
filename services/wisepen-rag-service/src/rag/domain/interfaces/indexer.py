"""Indexer 只读最终 Chunk 列表，拥有具体索引投影与写入。"""

from typing import Protocol

from rag.domain.models import Chunk


class Indexer(Protocol):
    async def index(self, chunks: list[Chunk]) -> None: ...
