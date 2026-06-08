from typing import Dict, List
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.indexing.indexers.qdrant_collection import QdrantCollectionConfig
from chat.application.rag.runtime.models import DenseVector, IndexingTextPair
from chat.application.rag.runtime.models import SearchChunk


class QdrantIndexError(RuntimeError):
    """Qdrant 索引写入失败。"""


class QdrantChunkIndexer:
    """Qdrant chunk indexer。

    - 负责把 SearchChunk dense vector 和 BM25 document 写入 Qdrant。
    - dense vector 由应用层 embedding client 生成。
    - BM25 vector 由 Qdrant 根据 Document 输入生成。
    - 每个 SearchChunk 对应一个 Qdrant point。
    - payload 用于后续强过滤。
    - 不负责 embedding。
    - 不负责检索。
    """

    def __init__(
        self,
        client: AsyncQdrantClient,
        config: QdrantCollectionConfig,
    ) -> None:
        """初始化对象依赖。"""
        self._client = client
        self._config = config

    async def upsert_chunks(
        self,
        user_id: str,
        index_version: str,
        search_chunks: List[SearchChunk],
        dense_vectors: List[DenseVector],
        indexing_text_pairs: Dict[str, IndexingTextPair],
    ) -> None:
        """批量写入 dense vector 和 Qdrant BM25 document。

        - search_chunks 和 dense_vectors 必须一一对应。
        - indexing_text_pairs 必须包含每个 search_chunk 的索引文本。
        - dense 使用 semantic_indexing_text 对应的向量。
        - bm25 使用 semantic_indexing_text 作为 Document text。
        - Qdrant upsert 是幂等写入。

        Args:
        - user_id: 用户 ID。
        - index_version: 当前索引版本。
        - search_chunks: 子块列表。
        - dense_vectors: dense vector 列表。
        - indexing_text_pairs: chunk_id 到双索引文本的映射。
        """

        if len(search_chunks) != len(dense_vectors):
            raise QdrantIndexError(
                "Search chunk count does not match dense vector count."
            )

        if not search_chunks:
            return

        points: List[models.PointStruct] = []
        for chunk, dense_vector in zip(search_chunks, dense_vectors):
            indexing_text_pair = indexing_text_pairs.get(chunk.chunk_id)
            if indexing_text_pair is None:
                raise QdrantIndexError(
                    f"Indexing text not found for search chunk: {chunk.chunk_id}"
                )

            points.append(
                models.PointStruct(
                    # 用 uuid_5 确保同一个chunk计算得到同一个UUID
                    id=str(
                        uuid5(
                            NAMESPACE_URL, (
                                f"wisepen-rag:"
                                f"{user_id}:"
                                f"{chunk.resource_kind.value}:"
                                f"{chunk.resource_id}:"
                                f"{index_version}:"
                                f"{chunk.chunk_id}"
                            )
                        )
                    ),
                    vector={
                        "dense": dense_vector,
                        "bm25": models.Document(
                            text=indexing_text_pair.semantic_indexing_text,
                            model="qdrant/bm25",
                        ),
                    },
                    payload={
                        "user_id": user_id,
                        "resource_kind": chunk.resource_kind.value,
                        "resource_id": chunk.resource_id,
                        "index_version": index_version,
                        "chunk_id": chunk.chunk_id,
                        "parent_chunk_id": chunk.parent_chunk_id,
                        "parent_chunk_index": chunk.parent_chunk_index,
                        "chunk_index": chunk.chunk_index,
                    },
                )
            )

        await self._client.upsert(
            collection_name=self._config.collection_name,
            points=points,
            wait=True,
        )

    async def delete_by_index_version(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
    ) -> None:
        """删除指定 resource 索引版本的 Qdrant points。

        - 用于 GC 或失败重建清理。
        - 不用于正常索引发布前删除旧版本。
        - Manifest 切换前旧版本仍可能服务线上检索。
        """

        await self._client.delete(
            collection_name=self._config.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id),
                        ),
                        models.FieldCondition(
                            key="resource_kind",
                            match=models.MatchValue(value=resource_kind.value),
                        ),
                        models.FieldCondition(
                            key="resource_id",
                            match=models.MatchValue(value=resource_id),
                        ),
                        models.FieldCondition(
                            key="index_version",
                            match=models.MatchValue(value=index_version),
                        ),
                    ]
                )
            ),
            wait=True,
        )

