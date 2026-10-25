"""加工阶段通过返回值传递 Chunk，可读取当前文档的策略。"""

from typing import Protocol

from rag.core.schema import Chunk, Document


class Transformer(Protocol):
    async def transform(
            self,
            chunks: list[Chunk],
            *,
            document: Document | None = None,
    ) -> list[Chunk]: ...
