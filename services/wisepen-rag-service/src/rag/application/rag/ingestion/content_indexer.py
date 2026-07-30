from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rag.application.rag.acl import RagResourceAclProjection
from rag.application.rag.repositories import (
    RagAclProjectionRepository,
    RagContentProjectionRepository,
    RagVectorIndexRepository,
)
from .context_indexing import ContextIndexingService
from .models import RagContentProjection, RagDocumentContent
from .revision import RagProjectionStage, RagProjectionStageAction, prepare_projection_stage
from .section_projector import RagSectionProjector

if TYPE_CHECKING:
    from rag.utils.llm_clients.embedding import EmbeddingClient


class RagContentIndexingError(RuntimeError):
    """依赖数据或索引写入未完成，Kafka consumer 应保留 offset。"""


@dataclass(frozen=True, slots=True)
class RagContentIndexResult:
    """一次内容索引任务的执行结果。"""

    stage: RagProjectionStage
    indexed_chunk_count: int
    embedded_chunk_count: int = 0
    reused_vector_count: int = 0


class RagContentIndexer:
    """协调文档投影、上下文生成、向量化和索引发布。"""

    __slots__ = (
        "_acl_repository",
        "_context_indexing",
        "_embedding_client",
        "_projection_repository",
        "_projector",
        "_vector_repository",
    )

    def __init__(
            self,
            *,
            projector: RagSectionProjector,
            projection_repository: RagContentProjectionRepository,
            vector_repository: RagVectorIndexRepository,
            acl_repository: RagAclProjectionRepository,
            embedding_client: EmbeddingClient,
            context_indexing: ContextIndexingService,
    ) -> None:
        self._projector = projector
        self._projection_repository = projection_repository
        self._vector_repository = vector_repository
        self._acl_repository = acl_repository
        self._embedding_client = embedding_client
        self._context_indexing = context_indexing

    async def index(self, content: RagDocumentContent) -> RagContentIndexResult:
        """构建并发布指定文档版本的 RAG 内容投影。"""
        projection = self._projector.project(content)

        # 先在本地投影结果上做一次轻量预检，尽早跳过旧消息和已应用消息。
        checkpoint = await self._projection_repository.get_checkpoint(projection.resource_id)
        preflight_stage = prepare_projection_stage(projection, checkpoint)

        if preflight_stage.action is RagProjectionStageAction.STALE:
            return RagContentIndexResult(stage=preflight_stage, indexed_chunk_count=0)

        if preflight_stage.action is RagProjectionStageAction.ALREADY_APPLIED:
            # 清理历史 revision，修复上次成功应用后清理中断的情况。
            await self._vector_repository.delete_other_revisions(
                resource_id=preflight_stage.resource_id,
                keep_content_revision=preflight_stage.content_revision,
            )
            return RagContentIndexResult(
                stage=preflight_stage,
                indexed_chunk_count=len(projection.retrieval_chunks),
            )

        # 向量索引必须携带 ACL；有 Chunk 时不允许在 ACL 缺失下继续写入。
        acl_projection = await self._load_acl_projection(projection)

        # indexing_context 会进入 embedding 输入，因此必须在 stage 和向量复用前完成。
        projection = await self._context_indexing.contextualize(projection)

        # 在仓储层再次判断，防止预检之后出现并发的新版本。
        stage = await self._projection_repository.stage_projection(projection)
        if stage.action is RagProjectionStageAction.STALE:
            return RagContentIndexResult(stage=stage, indexed_chunk_count=0)

        if stage.action is RagProjectionStageAction.ALREADY_APPLIED:
            # 预检时未应用，但 stage 时已应用：说明并发处理已成功覆盖，做相同的清理收尾。
            await self._vector_repository.delete_other_revisions(
                resource_id=stage.resource_id, keep_content_revision=stage.content_revision
            )
            return RagContentIndexResult(
                stage=stage,
                indexed_chunk_count=len(projection.retrieval_chunks),
            )

        # 优先复用输入未变化的向量，只为缺失 Chunk 调用 embedding 服务。
        reusable_vectors = await self._vector_repository.load_reusable_vectors(projection)
        dense_vectors, embedded_chunk_count = await self._embed(
            projection, reusable_vectors=reusable_vectors
        )

        # 先写入 staged revision，写入完成后再将 checkpoint 标记为 applied。
        await self._vector_repository.upsert_staged_projection(
            projection=projection,
            stage=stage,
            dense_vectors=dense_vectors,
            acl_projection=acl_projection,
        )
        await self._projection_repository.apply_projection(stage)

        # applied 成功后清理旧 revision；若清理失败，Kafka 重试可再次收敛。
        await self._vector_repository.delete_other_revisions(
            resource_id=stage.resource_id, keep_content_revision=stage.content_revision
        )

        return RagContentIndexResult(
            stage=stage,
            indexed_chunk_count=len(projection.retrieval_chunks),
            embedded_chunk_count=embedded_chunk_count,
            reused_vector_count=(len(projection.retrieval_chunks) - embedded_chunk_count),
        )

    async def _load_acl_projection(
            self, projection: RagContentProjection
    ) -> RagResourceAclProjection | None:
        """加载索引写入所需的 ACL 快照。

        空文档不会产生向量记录，因此无需强制加载 ACL。
        """
        if not projection.retrieval_chunks:
            return None

        # 优先读取本地投影，避免每次索引都访问权限权威源。
        acl_projection = await self._acl_repository.get_projection(projection.resource_id)
        if acl_projection is not None:
            return acl_projection

        # 本地缺失时回源加载，并补写本地 ACL 投影，避免下次再走权威源。
        acl_projection = await self._acl_repository.load_authoritative_projection(projection.resource_id)
        if acl_projection is None:
            raise RagContentIndexingError(f"resource ACL is unavailable for {projection.resource_id}")

        await self._acl_repository.upsert_projection(acl_projection)
        return acl_projection

    async def _embed(
            self,
            projection: RagContentProjection,
            *,
            reusable_vectors: Mapping[str, Sequence[float]],
    ) -> tuple[dict[str, Sequence[float]], int]:
        """复用已有向量，并批量生成剩余 Chunk 的稠密向量。"""
        if not projection.retrieval_chunks:
            return {}, 0

        dense_vectors: dict[str, Sequence[float]] = {
            chunk.chunk_id: reusable_vectors[chunk.chunk_id]
            for chunk in projection.retrieval_chunks
            if chunk.chunk_id in reusable_vectors
        }
        missing_chunks = tuple(
            chunk
            for chunk in projection.retrieval_chunks
            if chunk.chunk_id not in dense_vectors
        )
        if not missing_chunks:
            return dense_vectors, 0

        result = await self._embedding_client.aembed([chunk.index_text for chunk in missing_chunks])
        if len(result.embeddings) != len(missing_chunks):
            raise RagContentIndexingError("embedding response count does not match retrieval chunks")

        dense_vectors.update(
            {chunk.chunk_id: vector for chunk, vector in zip(missing_chunks, result.embeddings, strict=True)}
        )
        return dense_vectors, len(missing_chunks)
