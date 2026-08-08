from __future__ import annotations

from typing import TYPE_CHECKING

from common.logger import info
from rag.application.rag.acl import RagPermissionAuthorizer
from rag.domain.repositories import (
    RagCandidateRepository,
    RagContentCheckpointRepository,
)
from rag.utils.ranking import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingPipeline,
)
from .models import (
    RagCandidateRequest,
    RagRetrievalCandidate,
    RagRetrievalRequest,
    RagRetrievalResult,
    RagRetrievalStatus,
)

if TYPE_CHECKING:
    from rag.utils.llm_clients.embedding import EmbeddingClient


class RagRetrievalError(RuntimeError):
    """RAG 查询输入或依赖结果不符合检索契约。"""


_EMPTY_RETRIEVAL_RESULT = RagRetrievalResult(
    status=RagRetrievalStatus.IRRELEVANT,
    candidates=(),
)


class RagCandidateRetriever:
    """执行候选召回、版本校验、权限过滤和最终排序。"""

    __slots__ = (
        "_candidate_repository",
        "_checkpoint_repository",
        "_embedding_client",
        "_permission_authorizer",
        "_ranking_pipeline",
    )

    def __init__(
            self,
            *,
            embedding_client: EmbeddingClient,
            candidate_repository: RagCandidateRepository,
            checkpoint_repository: RagContentCheckpointRepository,
            permission_authorizer: RagPermissionAuthorizer,
            ranking_pipeline: RankingPipeline,
    ) -> None:
        self._embedding_client = embedding_client
        self._candidate_repository = candidate_repository
        self._checkpoint_repository = checkpoint_repository
        self._permission_authorizer = permission_authorizer
        self._ranking_pipeline = ranking_pipeline

    async def retrieve(
            self,
            request: RagRetrievalRequest,
    ) -> RagRetrievalResult:
        """执行完整 RAG 检索流程。"""
        semantic_query = request.semantic_query.strip()
        if not semantic_query:
            raise RagRetrievalError("semantic_query must not be empty")
        lexical_query = (
            request.lexical_query.strip()
            if request.lexical_query is not None
            else semantic_query
        )
        if not lexical_query:
            raise RagRetrievalError("lexical_query must not be empty when provided")
        if not request.permission_scope.user_id.strip():
            raise RagRetrievalError("permission scope user_id must not be empty")
        if request.top_k <= 0 or request.candidate_limit <= 0:
            return _EMPTY_RETRIEVAL_RESULT

        # 词法查询不会进入 embedding；它可能是缺少语法结构的标识符和术语集合。
        embedding = await self._embedding_client.aembed([semantic_query])
        if len(embedding.embeddings) != 1:
            raise RagRetrievalError("query embedding response must contain one vector")

        candidates = await self._candidate_repository.retrieve_candidates(
            RagCandidateRequest(
                lexical_query=lexical_query,
                semantic_vector=embedding.embeddings[0],
                permission_scope=request.permission_scope,
                resource_ids=request.resource_ids,
                limit=request.candidate_limit,
            )
        )
        if not candidates:
            return _EMPTY_RETRIEVAL_RESULT

        # 版本过滤：只接受当前已经成功投影完成的内容版本，
        # 防止召回到旧 chunk（已被 delete_other_revisions 清理）或尚未应用的新版本。
        applied_revisions = await self._checkpoint_repository.get_applied_revisions(
            [candidate.resource_id for candidate in candidates]
        )
        candidates = tuple(
            candidate
            for candidate in candidates
            if applied_revisions.get(candidate.resource_id) == candidate.content_revision
        )
        if not candidates:
            return _EMPTY_RETRIEVAL_RESULT

        # 最终权限门：即使底层召回带有 ACL filter，也再次基于本地权威投影确认。
        accessible_resource_ids = await self._permission_authorizer.accessible_resource_ids(
            resource_ids=(candidate.resource_id for candidate in candidates),
            scope=request.permission_scope,
        )
        candidates = tuple(
            candidate for candidate in candidates if candidate.resource_id in accessible_resource_ids
        )
        if not candidates:
            return _EMPTY_RETRIEVAL_RESULT

        # prior_rank 与 group_key 在排序层共同决定"同资源去重"和"跨资源融合"。
        ranking = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=semantic_query),
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
                # ReadingBlock 去重发生在 chunk 排序之后，因此先保留候选水位内的完整排序。
                top_k=request.candidate_limit,
                candidate_limit=request.candidate_limit,
            )
        )

        candidates_by_id = {candidate.chunk_id: candidate for candidate in candidates}

        if ranking.decision is None:
            raise RagRetrievalError(
                "knowledge search ranking pipeline did not produce a relevance decision"
            )

        # RetrievalChunk 只是评分单位；最终窗口按 ReadingBlock 去重，避免同一窗口
        # 的相邻 chunk 占满 top-k，同时保留同一 Section 中独立命中的多个窗口。
        selected: list[RagRetrievalCandidate] = []
        selected_block_keys: set[tuple[str, str]] = set()
        for item in ranking.ranked:
            candidate = candidates_by_id.get(item.candidate_id)
            if candidate is None:
                continue
            block_key = (candidate.resource_id, candidate.reading_block_id)
            if block_key in selected_block_keys:
                continue
            selected_block_keys.add(block_key)
            selected.append(candidate)
            if len(selected) == request.top_k:
                break

        status = RagRetrievalStatus(ranking.decision.value)
        info(
            "rag retrieval relevance evaluated.",
            status=status.value,
            decision_score=ranking.decision_score,
            total_candidate_count=ranking.total_candidates,
            selected_reading_block_count=len(selected),
            resource_scoped=bool(request.resource_ids),
        )
        return RagRetrievalResult(
            status=status,
            candidates=tuple(selected),
        )
