"""摄入阶段的有序编排；不管理组件内部 I/O、重试或事务。"""

from rag.core.schema import Document
from rag.ingestion.chunkers.base import Chunker
from rag.ingestion.indexers.base import Indexer
from rag.ingestion.transformers.base import Transformer


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

    async def run(self, document: Document) -> None:
        chunks = await self.chunker.split(document)

        for transformer in self.transformers:
            chunks = await transformer.transform(chunks, document=document)

        # 不捕获异常：后续阶段立即停止，已成功写入的投影由调用方处理。
        for indexer in self.indexers:
            await indexer.index(chunks)
