"""普通向量检索用例；授权回查完成后，正文才能进入重排与结果组装。"""

from typing import Literal

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import (
    Candidate,
    Fusion,
    MmrDiversity,
    Reranker,
    RetrievalPipeline,
)
from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.filters import MetadataFilters
from rag.domain.storage.graph import GraphReader
from rag.domain.storage.vector import VectorMatch, VectorReader
from .hit import ChunkHit, PreparedChunk, build_chunk_hits
from .retriever import ChunkRetriever, ChunkRetrievalMode


class ChunkRetrieval:
    """共享固定依赖；每次调用构造 Retriever/Pipeline，显式使用调用方快照。"""

    def __init__(
        self,
        *,
        snapshot_reader: ResourceSnapshotReader,
        vector_reader: VectorReader,
        embedding_client: EmbeddingClient,
        fusion: Fusion[VectorMatch] | None = None,
        reranker: Reranker[PreparedChunk] | None = None,
        graph_reader: GraphReader | None = None,
    ) -> None:
        self._snapshot_reader = snapshot_reader
        self._vector_reader = vector_reader
        self._embedding_client = embedding_client
        self._fusion = fusion
        self._reranker = reranker
        self._graph_reader = graph_reader

    async def retrieve(
        self,
        query: str,
        *,
        snapshot: ResourceSnapshot,
        mode: ChunkRetrievalMode = ChunkRetrievalMode.HYBRID,
        top_k: int = 5,
        focus: str = "",
        diversity: Literal["off", "moderate", "high"] = "off",
        filters: MetadataFilters | None = None,
        dense_top_k: int | None = None,
        sparse_top_k: int | None = None,
        candidate_limit: int = 60,
    ) -> list[ChunkHit]:
        """一次召回、授权准备和排序；授权过滤后不补召回或重新观察发布/ACL。"""
        include_embedding = diversity != "off"
        # 已连接的仓储与客户端共享；本次只组装固定配置的召回组件，不新建连接。
        retriever = ChunkRetriever.from_storage(
            reader=self._vector_reader,
            embedding_client=self._embedding_client,
            mode=mode,
            fusion=self._fusion,
            dense_top_k=dense_top_k,
            sparse_top_k=sparse_top_k,
            candidate_limit=candidate_limit,
            filters=filters,
            include_embedding=include_embedding,
        )

        async def prepare(
            candidates: list[Candidate[VectorMatch]],
        ) -> list[Candidate[PreparedChunk]]:
            return await self._prepare(candidates, snapshot=snapshot)

        pipeline = RetrievalPipeline[VectorMatch, PreparedChunk](
            retriever=retriever,
            prepare_candidates=prepare,
            reranker=self._reranker,
            diversity=MmrDiversity(vector_getter=_stored_embedding),
        )
        ranked = await pipeline.run(
            query=query, top_k=top_k, focus=focus, diversity=diversity
        )
        return await build_chunk_hits(
            ranked,
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
        )

    async def _prepare(
        self,
        candidates: list[Candidate[VectorMatch]],
        *,
        snapshot: ResourceSnapshot,
    ) -> list[Candidate[PreparedChunk]]:
        """先筛除无定位投影，再批量授权回查；正文不能反向修补错误 metadata。"""
        located = [
            item
            for item in candidates
            if all(
                isinstance(value, str) and value.strip()
                for value in (
                    item.payload.metadata.get("resource_id"),
                    item.payload.metadata.get("content_revision"),
                )
            )
        ]
        if not located:
            return []
        chunks = await self._snapshot_reader.read_chunks(
            list(dict.fromkeys(item.payload.record_id for item in located)),
            snapshot=snapshot,
        )
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        prepared = []
        for item in located:
            chunk = by_id.get(item.payload.record_id)
            if (
                chunk is None
                or item.payload.metadata["resource_id"] != chunk.resource_id
                or item.payload.metadata["content_revision"] != chunk.content_revision
            ):
                continue
            prepared.append(
                Candidate(
                    candidate_id=item.candidate_id,
                    payload=PreparedChunk(chunk, item.payload.embedding),
                    rank=len(prepared) + 1,
                    score=item.score,
                )
            )
        return prepared


def _stored_embedding(payload: PreparedChunk) -> list[float]:
    # Qdrant 在读取边界校验向量；其他 VectorReader 也必须履行按需返回合同。
    if payload.embedding is None:
        raise RuntimeError("missing requested vector match embedding")
    return payload.embedding
