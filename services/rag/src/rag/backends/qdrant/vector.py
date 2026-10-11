"""Collection-bound 向量后端，保存可重建投影并等待实际写入完成。"""

from math import isfinite
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from rag.core.schema.filters import MetadataFilters
from rag.core.schema.vector import VectorMatch, VectorQuery, VectorRecord

from .filters import compile_filters


class QdrantVectorStorage:
    """只执行向量 CRUD；client 须启用 cloud_inference 以转发服务端 BM25。"""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection: str,
        dense_vector_size: int,
        dense_vector_name: str = "dense",
        sparse_vector_name: str = "sparse",
        batch_size: int = 256,
    ) -> None:
        if dense_vector_size <= 0 or batch_size <= 0:
            raise ValueError("vector size and batch size must be positive")
        if not collection or not dense_vector_name or not sparse_vector_name:
            raise ValueError("collection and vector names must not be empty")
        if dense_vector_name == sparse_vector_name:
            raise ValueError("dense and sparse vector names must differ")
        self._client = client
        self._collection = collection
        self._dense_vector_size = dense_vector_size
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._batch_size = batch_size

    async def ensure_collection(self) -> None:
        """显式创建或核对 collection，保证必要索引；配置冲突不删除或重建集合。"""
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config={
                    self._dense_vector_name: models.VectorParams(
                        size=self._dense_vector_size, distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    self._sparse_vector_name: models.SparseVectorParams(
                        modifier=models.Modifier.IDF
                    )
                },
            )
        info = await self._client.get_collection(self._collection)
        params = info.config.params
        dense = (
            params.vectors.get(self._dense_vector_name)
            if isinstance(params.vectors, dict)
            else None
        )
        sparse = (params.sparse_vectors or {}).get(self._sparse_vector_name)
        if (
            dense is None
            or dense.size != self._dense_vector_size
            or dense.distance != models.Distance.COSINE
            or sparse is None
            or sparse.modifier != models.Modifier.IDF
        ):
            raise ValueError(f"incompatible vector configuration: {self._collection}")

        # 每次显式初始化都补齐索引，允许上次初始化在创建集合后部分失败。
        for field in ("metadata.resource_id", "metadata.content_revision"):
            result = await self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
            if result.status != models.UpdateStatus.COMPLETED:
                raise RuntimeError(f"payload index creation did not complete: {field}")

    async def upsert(self, records: list[VectorRecord]) -> None:
        """整条 point 替换；每批同时写两类向量，部分成功保留，不隐式初始化。"""
        for start in range(0, len(records), self._batch_size):
            points = [
                models.PointStruct(
                    # Qdrant 不接受任意字符串 ID；业务原 ID 保存在 payload 中。
                    id=str(
                        uuid5(
                            NAMESPACE_URL, f"wisepen-rag-v4:vector:{record.record_id}"
                        )
                    ),
                    vector={
                        self._dense_vector_name: record.embedding,
                        self._sparse_vector_name: models.Document(
                            text=record.text,
                            model="qdrant/bm25",
                            options={"tokenizer": "multilingual"},
                        ),
                    },
                    payload={
                        "record_id": record.record_id,
                        "metadata": record.metadata,
                    },
                )
                for record in records[start : start + self._batch_size]
            ]
            result = await self._client.upsert(
                collection_name=self._collection, points=points, wait=True
            )
            # acknowledged 只说明进入队列，不能作为必需 Indexer 的成功返回。
            if result.status != models.UpdateStatus.COMPLETED:
                raise RuntimeError("vector upsert did not complete")

    async def query(self, request: VectorQuery) -> list[VectorMatch]:
        if (request.embedding is None) == (request.text is None):
            raise ValueError("query requires exactly one of embedding or text")
        if request.top_k <= 0:
            raise ValueError("top_k must be positive")
        response = await self._client.query_points(
            collection_name=self._collection,
            query=(
                request.embedding
                if request.embedding is not None
                else models.Document(
                    text=request.text,
                    model="qdrant/bm25",
                    options={"tokenizer": "multilingual"},
                )
            ),
            using=(
                self._dense_vector_name
                if request.embedding is not None
                else self._sparse_vector_name
            ),
            query_filter=(
                compile_filters(request.filters)
                if request.filters is not None
                else None
            ),
            limit=request.top_k,
            with_payload=["record_id", "metadata"],
            # 即使是 sparse 召回，多样化也使用命中已有的 dense 向量。
            with_vectors=[self._dense_vector_name]
            if request.include_embedding
            else False,
        )
        matches = []
        for point in response.points:
            payload = point.payload or {}
            record_id, metadata = payload.get("record_id"), payload.get("metadata")
            # 库中的坏投影不能伪装成无候选或用 point UUID 代替原业务身份。
            if (
                not isinstance(record_id, str)
                or not record_id
                or not isinstance(metadata, dict)
            ):
                raise RuntimeError("invalid vector match payload")
            embedding = None
            if request.include_embedding:
                embedding = (
                    point.vector.get(self._dense_vector_name)
                    if isinstance(point.vector, dict)
                    else None
                )
                # 请求向量后缺失或坏数据必须暴露，不能用空向量掩盖后端错误。
                if (
                    not isinstance(embedding, list)
                    or len(embedding) != self._dense_vector_size
                    or any(
                        type(value) not in (int, float) or not isfinite(value)
                        for value in embedding
                    )
                ):
                    raise RuntimeError("invalid vector match embedding")
            matches.append(VectorMatch(record_id, point.score, metadata, embedding))
        return matches

    async def delete(self, filters: MetadataFilters) -> None:
        result = await self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(filter=compile_filters(filters)),
            wait=True,
        )
        if result.status != models.UpdateStatus.COMPLETED:
            raise RuntimeError("vector delete did not complete")
