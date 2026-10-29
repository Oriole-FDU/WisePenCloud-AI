"""分块阶段只确定正文边界、顺序和结构归属。"""

from typing import Protocol

from rag.domain.models import Chunk, Document


class Chunker(Protocol):
    async def split(self, document: Document) -> list[Chunk]: ...
