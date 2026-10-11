"""加工阶段通过返回值传递 Chunk，可读取当前文档的策略。"""

from typing import Protocol

from rag.core.schema import Document
from rag.ingestion.chunkers.base import Chunk


class Transformer(Protocol):
    async def transform(
        self,
        chunks: list[Chunk],
        *,
        document: Document | None = None,
    ) -> list[Chunk]: ...
