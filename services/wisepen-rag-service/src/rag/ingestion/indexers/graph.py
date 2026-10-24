"""已保存图事实的拓扑与来源向量索引，只读输入，不管理 publication。"""

from dataclasses import replace
from hashlib import sha256

from common.utils.ai_clients.embedding import EmbeddingClient

from rag.core.schema import Chunk, Ontology
from rag.core.schema.filters import FilterOperator, MetadataFilter, MetadataFilters
from rag.storage.graph import (
    GraphSourceProjection,
    GraphVectorKind,
    GraphWriter,
    entity_vector_text,
    graph_fact_sha256,
    graph_vector_record_id,
    relationship_vector_text,
)
from rag.storage.vector import VectorRecord, VectorWriter

from .base import Indexer


class GraphIndexer(Indexer):
    """一次校验后依次写拓扑和向量；各 writer 的部分成功保留。

    两类投影可独立启用。向量来源批先完成 embedding，再清旧写新；
    """

    def __init__(
        self,
        *,
        ontology: Ontology,
        writer: GraphWriter | None = None,
        embedding_client: EmbeddingClient | None = None,
        entity_writer: VectorWriter | None = None,
        relationship_writer: VectorWriter | None = None,
        source_batch_size: int = 64,
        embedding_batch_size: int = 32,
    ) -> None:
        # 向量投影三件套必须同时配置或同时缺省
        configured = (embedding_client, entity_writer, relationship_writer)
        if any(value is not None for value in configured) and any(
            value is None for value in configured
        ):
            raise ValueError(
                "graph vectors require embedding client and both vector writers"
            )

        # 至少要有一个投影 writer，否则整个索引无意义
        if writer is None and embedding_client is None:
            raise ValueError("at least one graph projection writer is required")

        self._writer = writer
        self._ontology = ontology
        self._embedding_client = embedding_client
        self._entity_writer = entity_writer
        self._relationship_writer = relationship_writer
        self._source_batch_size = source_batch_size
        self._embedding_batch_size = embedding_batch_size

    async def index(self, chunks: list[Chunk]) -> None:
        # 两种投影共用一次事实解析；后面的结构错误不能发生在首笔写入之后。
        facts = [self._ontology.parse_chunk_facts(chunk) for chunk in chunks]

        # 先写拓扑投影
        if self._writer is not None and chunks:
            projections = [
                GraphSourceProjection(
                    chunk_id=chunk.chunk_id,
                    metadata={
                        "resource_id": chunk.resource_id,
                        "content_revision": chunk.content_revision,
                        "ontology_name": self._ontology.domain,
                    },
                    nodes=list(nodes.values()),
                    edges=list(edges.values()),
                )
                for chunk, (nodes, edges) in zip(chunks, facts, strict=True)
            ]
            await self._writer.replace_sources(projections)

        # 再写向量投影（如果启用）
        if self._embedding_client is not None:
            await self._index_vectors(chunks, facts)

    async def _index_vectors(self, chunks: list[Chunk], facts: list) -> None:
        # 按来源批处理，避免一次性占用过多内存与模型配额
        for start in range(0, len(chunks), self._source_batch_size):
            batch = chunks[start : start + self._source_batch_size]

            entity_records = []
            relationship_records = []

            # 组装本批的实体与关系向量记录（先不带 embedding）
            for chunk, (nodes, edges) in zip(
                batch, facts[start : start + len(batch)], strict=True
            ):
                for kind, items, records in (
                    (GraphVectorKind.ENTITY, nodes.values(), entity_records),
                    (
                        GraphVectorKind.RELATIONSHIP,
                        edges.values(),
                        relationship_records,
                    ),
                ):
                    for item in items:
                        # 实体与关系使用不同的向量文本模板
                        text = (
                            entity_vector_text(item, self._ontology)
                            if kind is GraphVectorKind.ENTITY
                            else relationship_vector_text(item, nodes, self._ontology)
                        )
                        records.append(
                            VectorRecord(
                                record_id=graph_vector_record_id(
                                    kind, chunk.chunk_id, item.id
                                ),
                                text=text,
                                embedding=[],  # 当前批 embedding 全部成功后才交给 Writer。
                                metadata={
                                    "chunk_id": chunk.chunk_id,
                                    "resource_id": chunk.resource_id,
                                    "content_revision": chunk.content_revision,
                                    "ontology_name": self._ontology.domain,
                                    "fact_id": item.id,
                                    "text_sha256": sha256(
                                        text.encode("utf-8")
                                    ).hexdigest(),  # 用于校验文本一致性
                                    "fact_sha256": graph_fact_sha256(item, nodes),
                                    "graph_version": chunk.metadata.get(
                                        "graph_version"
                                    ),
                                },
                            )
                        )

            records = entity_records + relationship_records
            embedded = []

            # 分批调用 embedding；响应合同由 Common 核对，失败转为可重试错误
            for offset in range(0, len(records), self._embedding_batch_size):
                current = records[offset : offset + self._embedding_batch_size]
                try:
                    vectors = await self._embedding_client.embed(
                        texts=[record.text for record in current]
                    )
                except ValueError as exc:
                    raise RuntimeError("invalid graph embedding response") from exc

                embedded.extend(
                    replace(record, embedding=vector)
                    for record, vector in zip(current, vectors, strict=True)
                )

            # 整个来源批的模型响应已合法，才开始不可原子化的两集合替换。
            filters = MetadataFilters(
                [
                    MetadataFilter(
                        "chunk_id",
                        [chunk.chunk_id for chunk in batch],
                        FilterOperator.IN,
                    )
                ]
            )
            await self._entity_writer.delete(filters)  # 清旧实体向量
            await self._relationship_writer.delete(filters)  # 清旧关系向量

            # 前半是实体、后半是关系，按 split 切分写回
            split = len(entity_records)
            if split:
                await self._entity_writer.upsert(embedded[:split])
            if relationship_records:
                await self._relationship_writer.upsert(embedded[split:])
