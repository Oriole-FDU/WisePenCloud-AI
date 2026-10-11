"""从最终 Chunk 派生 dense 与 sparse 输入，按批等待全部必需向量投影成功。"""

from math import isfinite

from common.utils.ai_clients.embedding import EmbeddingClient

from rag.core.schema import Chunk, VectorRecord
from rag.storage.vector import VectorWriter

from .base import Indexer


class VectorIndexer(Indexer):
    """只读 Chunk；分批 embedding 后写入投影，不管理发布或重试。"""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        writer: VectorWriter,
        batch_size: int = 32,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch size must be positive")
        self._embedding_client = embedding_client
        self._writer = writer
        self._batch_size = batch_size

    async def index(self, chunks: list[Chunk]) -> None:
        """每批先 embedding 后写入；失败停止后续批次，已完成的事实与投影保留。"""
        for start in range(0, len(chunks), self._batch_size):
            batch = chunks[start : start + self._batch_size]
            texts = [chunk.retrieval_text for chunk in batch]

            try:
                vectors = await self._embedding_client.embed(texts=texts)
            except ValueError as exc:
                # Common 的响应数量错误是 ValueError，不是上游事件的永久输入错误
                raise RuntimeError("invalid embedding response") from exc

            # 数量、维度、取值必须全部匹配；任一不合规都视为供应商响应异常
            if len(vectors) != len(batch) or any(
                (
                    self._embedding_client.dimensions is not None
                    and len(vector) != self._embedding_client.dimensions
                )
                or any(
                    type(value) not in (int, float) or not isfinite(value)
                    for value in vector
                )
                for vector in vectors
            ):
                raise RuntimeError(
                    "invalid embedding response count, dimensions or values"
                )

            records = [
                VectorRecord(
                    record_id=chunk.chunk_id,
                    text=text,
                    embedding=vector,
                    metadata={
                        "resource_id": chunk.resource_id,
                        "content_revision": chunk.content_revision,
                    },
                )
                for chunk, text, vector in zip(batch, texts, vectors, strict=True)
            ]
            await self._writer.upsert(records)
