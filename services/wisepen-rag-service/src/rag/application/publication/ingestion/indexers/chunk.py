"""通过内容 Writer 保存加工后的 Chunk 事实，不构建检索投影。"""

from rag.domain.interfaces import Indexer
from rag.domain.models import Chunk
from rag.domain.storage.content import ContentWriter


class ChunkFactsIndexer(Indexer):
    """只读共享 Chunk 输入，内容写入失败直接交给 Pipeline 传播。"""

    def __init__(self, writer: ContentWriter) -> None:
        self._writer = writer

    async def index(self, chunks: list[Chunk]) -> None:
        await self._writer.upsert_chunks(chunks)
