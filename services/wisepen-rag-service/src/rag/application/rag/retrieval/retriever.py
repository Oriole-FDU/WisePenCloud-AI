from __future__ import annotations

from typing import TYPE_CHECKING

from rag.application.rag.acl import RagPermissionAuthorizer
from rag.application.rag.repositories import (
    RagCandidateRepository,
    RagContentProjectionRepository,
)
from common.utils.ranking import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingPipeline,
)
from .models import RagCandidateRequest, RagRetrievalCandidate, RagRetrievalRequest

if TYPE_CHECKING:
    from rag.utils.llm_clients.embedding import EmbeddingClient


class RagRetrievalError(RuntimeError):
    """RAG 查询输入或依赖结果不符合检索契约。"""


class RagCandidateRetriever:
    """执行候选召回、版本校验、权限过滤和最终排序。"""

    __slots__ = (
        "_candidate_repository",
        "_embedding_client",
        "_permission_authorizer",
        "_projection_repository",
        "_ranking_pipeline",
    )

    def __init__(
            self,
            *,
            embedding_client: EmbeddingClient,
            candidate_repository: RagCandidateRepository,
            projection_repository: RagContentProjectionRepository,
            permission_authorizer: RagPermissionAuthorizer,
            ranking_pipeline: RankingPipeline,
    ) -> None:
        self._embedding_client = embedding_client
        self._candidate_repository = candidate_repository
        self._projection_repository = projection_repository
        self._permission_authorizer = permission_authorizer
        self._ranking_pipeline = ranking_pipeline

    async def retrieve(
            self,
            request: RagRetrievalRequest,
    ) -> tuple[RagRetrievalCandidate, ...]:
        """执行完整 RAG 检索流程。"""
        query = request.query.strip()
        if not query:
            raise RagRetrievalError("query must not be empty")
        if not request.permission_scope.user_id.strip():
            raise RagRetrievalError("permission scope user_id must not be empty")
        if request.top_k <= 0 or request.candidate_limit <= 0:
            return ()

        # Query embedding 只生成一次，供后续向量召回使用。
        embedding = await self._embedding_client.aembed([query])
        if len(embedding.embeddings) != 1:
            raise RagRetrievalError("query embedding response must contain one vector")

        candidates = await self._candidate_repository.retrieve_candidates(
            RagCandidateRequest(
                query_text=query,
                query_vector=embedding.embeddings[0],
                permission_scope=request.permission_scope,
                resource_ids=request.resource_ids,
                limit=request.candidate_limit,
            )
        )
        if not candidates:
            return ()

        # 版本过滤：只接受当前已经成功投影完成的内容版本，
        # 防止召回到旧 chunk（已被 delete_other_revisions 清理）或尚未应用的新版本。
        applied_revisions = await self._projection_repository.get_applied_revisions(
            [candidate.resource_id for candidate in candidates]
        )
        candidates = tuple(
            candidate
            for candidate in candidates
            if applied_revisions.get(candidate.resource_id) == candidate.content_revision
        )
        if not candidates:
            return ()

        # 最终权限门：即使底层召回带有 ACL filter，也再次基于本地权威投影确认。
        accessible_resource_ids = await self._permission_authorizer.accessible_resource_ids(
            resource_ids=(candidate.resource_id for candidate in candidates),
            scope=request.permission_scope,
        )
        candidates = tuple(
            candidate for candidate in candidates if candidate.resource_id in accessible_resource_ids
        )
        if not candidates:
            return ()

        # prior_rank 与 group_key 在排序层共同决定"同资源去重"和"跨资源融合"。
        ranking = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=query),
                candidates=tuple(
                    RankCandidate(
                        candidate_id=candidate.chunk_id,
                        text=candidate.raw_text,
                        fields={
                            "section": " > ".join(candidate.section_path),
                            "anchor": "\n".join(candidate.anchor_labels),
                        },
                        # 召回顺序作为排序先验，索引越小越靠前。
                        prior_rank=index,
                        # 同资源候选限制由排序层处理。
                        group_key=candidate.resource_id,
                    )
                    for index, candidate in enumerate(candidates, start=1)
                ),
                # 把候选携带的召回信号（Qdrant RRF、Neo4j boost 等）一并交给排序层做融合。
                signals=tuple(signal for candidate in candidates for signal in candidate.signals),
                top_k=request.top_k,
                candidate_limit=request.candidate_limit,
            )
        )

        candidates_by_id = {candidate.chunk_id: candidate for candidate in candidates}

        # 排序结果只引用仍然存在且已通过前置校验的候选，防止排序层引到被过滤掉的 chunk。
        return tuple(
            candidates_by_id[item.candidate_id]
            for item in ranking.ranked
            if item.candidate_id in candidates_by_id
        )
