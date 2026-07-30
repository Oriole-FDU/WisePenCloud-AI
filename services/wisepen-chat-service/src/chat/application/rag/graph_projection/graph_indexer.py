from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chat.application.rag.graph_extraction import KnowledgeGraphExtractor, build_extraction_windows
from chat.application.rag.repositories import (
    KnowledgeGraphProjectionRepository,
    KnowledgeGraphProjectionSupersededError,
    RagAclProjectionRepository,
    RagContentProjectionRepository,
)
from .projector import build_knowledge_graph_projection


class KnowledgeGraphIndexingError(RuntimeError):
    """关系投影依赖未就绪，Kafka 消费应重试当前正文事件。"""


class KnowledgeGraphIndexAction(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class KnowledgeGraphIndexResult:
    relation_revision: str | None
    action: KnowledgeGraphIndexAction
    projected_relation_count: int = 0


class KnowledgeGraphIndexer:
    """根据已生效的正文投影构建并提交知识图谱投影。"""

    __slots__ = (
        "_acl_repository",
        "_content_repository",
        "_extractor",
        "_graph_repository",
    )

    def __init__(
            self,
            *,
            content_repository: RagContentProjectionRepository,
            acl_repository: RagAclProjectionRepository,
            extractor: KnowledgeGraphExtractor,
            graph_repository: KnowledgeGraphProjectionRepository,
    ) -> None:
        self._content_repository = content_repository
        self._acl_repository = acl_repository
        self._extractor = extractor
        self._graph_repository = graph_repository

    async def index(self, *, resource_id: str, content_revision: str) -> KnowledgeGraphIndexResult:
        """为指定正文版本构建知识图谱投影。"""
        if await self._graph_repository.is_projection_applied(
                resource_id=resource_id, content_revision=content_revision
        ):
            return KnowledgeGraphIndexResult(
                relation_revision=None,
                action=KnowledgeGraphIndexAction.ALREADY_APPLIED,
            )

        source = await self._content_repository.load_applied_extraction_source(resource_id)
        checkpoint = await self._content_repository.get_checkpoint(resource_id)
        if (
                source is None
                or checkpoint is None
                or source.content_revision != content_revision
                or checkpoint.applied_content_revision != content_revision
        ):
            raise KnowledgeGraphIndexingError(
                f"applied content projection is unavailable for {resource_id}"
            )

        # 新正文生效后立即使旧关系失效，避免抽取期间继续暴露过期关系。
        await self._graph_repository.invalidate_projection(
            resource_id=resource_id, content_revision=content_revision
        )

        windows = build_extraction_windows(source)
        extractions = await self._extractor.extract(windows)
        projection = build_knowledge_graph_projection(
            resource_id=resource_id, content_revision=content_revision, extractions=extractions
        )

        # LLM 抽取期间正文可能再次更新，提交前重新校验版本。
        checkpoint = await self._content_repository.get_checkpoint(resource_id)
        if checkpoint is None or checkpoint.applied_content_revision != content_revision:
            return KnowledgeGraphIndexResult(
                relation_revision=None,
                action=KnowledgeGraphIndexAction.STALE,
            )

        acl_projection = await self._acl_repository.get_projection(resource_id)
        if acl_projection is None:
            acl_projection = await self._acl_repository.load_authoritative_projection(resource_id)
        if acl_projection is None:
            raise KnowledgeGraphIndexingError(f"resource ACL is unavailable for {resource_id}")
        await self._graph_repository.update_acl_projection(acl_projection)

        try:
            await self._graph_repository.apply_projection(projection=projection)
        except KnowledgeGraphProjectionSupersededError:
            # 仓储层负责最终并发校验，防止旧版本覆盖新版本。
            return KnowledgeGraphIndexResult(
                relation_revision=None,
                action=KnowledgeGraphIndexAction.STALE,
            )

        return KnowledgeGraphIndexResult(
            relation_revision=projection.relation_revision,
            action=KnowledgeGraphIndexAction.APPLIED,
            projected_relation_count=len(projection.edges),
        )
