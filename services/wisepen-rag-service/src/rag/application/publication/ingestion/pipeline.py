"""摄入阶段的有序编排；不管理组件内部 I/O、重试或事务。"""

from rag.domain.interfaces.chunker import Chunker
from rag.domain.interfaces.indexer import Indexer
from rag.domain.interfaces.transformer import Transformer
from rag.domain.models import Chunk, Document


class IngestionPipeline:
    """按配置串行执行；输入不复制，失败后停止且不回滚已完成写入。"""

    def __init__(
        self,
        *,
        chunker: Chunker,
        transformers: list[Transformer],
        indexers: list[Indexer],
    ) -> None:
        self.chunker = chunker
        self.transformers = transformers
        self.indexers = indexers

    async def run(self, document: Document) -> list[Chunk]:
        chunks = await self.chunker.split(document)

        for transformer in self.transformers:
            chunks = await transformer.transform(chunks, document=document)

        # 不捕获异常：后续阶段立即停止，已成功写入的投影由调用方处理。
        for indexer in self.indexers:
            await indexer.index(chunks)

        # 只有全部写入成功才交出最终列表，供外部 publication 核对预期集合。
        return chunks
