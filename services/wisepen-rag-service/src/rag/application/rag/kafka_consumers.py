from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from rag.application.rag.acl import RagAclProjectionError, RagResourceAclProjection
from rag.application.rag.graph_projection import KnowledgeGraphIndexer
from rag.application.rag.ingestion import (
    RagContentIndexer,
    RagContentIndexResult,
    RagContentProjectionMode,
    RagDocumentContent,
    RagProjectionStageAction,
)
from rag.domain.repositories import (
    RagAclProjectionRepository,
    RagAclProjectionTarget,
)
from common.logger import info, warn

_NonEmptyString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class DocumentReadyMessageError(ValueError):
    """DocumentReadyMessage 不符合 Java Kafka 正文契约。"""


class RagResourceDeletionError(ValueError):
    """ResourceDeletedMessage 不符合 Java Kafka 契约。"""


class RagResourceDeletionTarget(Protocol):
    """RAG 派生数据删除目标。"""

    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None: ...


class _DocumentReadyMessage(BaseModel):
    """文档内容变更事件。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    resource_id: _NonEmptyString = Field(alias="resourceId")
    version: Annotated[int, Field(strict=True, ge=1)]
    content: Annotated[str, Field(strict=True)]


class _AclRecalculateMessage(BaseModel):
    """ACL 权限重新计算事件。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    resource_id: _NonEmptyString = Field(alias="resourceId")


class _ResourceDeletedMessage(BaseModel):
    """资源物理删除事件。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    typed_resource_ids: dict[_NonEmptyString, list[_NonEmptyString]] = Field(alias="typedResourceIds")

    @property
    def resource_ids(self) -> tuple[str, ...]:
        """提取去重后的资源 ID。"""
        resource_ids = (
            resource_id
            for values in self.typed_resource_ids.values()
            for resource_id in values
        )
        return tuple(dict.fromkeys(resource_ids))


class RagDocumentReadyConsumer:
    """消费文档完成事件，更新内容投影和知识图谱投影。"""

    __slots__ = ("_content_indexer", "_graph_indexer")

    def __init__(self, *, content_indexer: RagContentIndexer, graph_indexer: KnowledgeGraphIndexer) -> None:
        self._content_indexer = content_indexer
        self._graph_indexer = graph_indexer

    async def handle(self, payload: Mapping[str, Any]) -> None:
        result = await self.index(payload)

        relation_result = None

        # 非结构化正文只保留朴素混合检索；图谱仓储负责清理旧 revision 并记录跳过状态。
        if result.stage.action is not RagProjectionStageAction.STALE:
            graph_operation = (
                self._graph_indexer.index
                if result.projection_mode is RagContentProjectionMode.SECTIONED
                else self._graph_indexer.skip
            )
            relation_result = await graph_operation(
                resource_id=result.stage.resource_id,
                content_revision=result.stage.content_revision,
            )

        info(
            "rag document content projection updated.",
            resource_id=result.stage.resource_id,
            document_version=result.stage.document_version,
            content_revision=result.stage.content_revision,
            projection_mode=result.projection_mode.value,
            action=result.stage.action.value,
            indexed_chunk_count=result.indexed_chunk_count,
            embedded_chunk_count=result.embedded_chunk_count,
            reused_vector_count=result.reused_vector_count,
            relation_action=relation_result.action.value if relation_result is not None else None,
            relation_revision=relation_result.relation_revision if relation_result is not None else None,
            projected_relation_count=(
                relation_result.projected_relation_count if relation_result is not None else 0
            ),
        )

    async def index(self, payload: Mapping[str, Any]) -> RagContentIndexResult:
        """解析事件并触发内容索引。"""
        try:
            message = _DocumentReadyMessage.model_validate(payload)
        except ValidationError as error:
            raise DocumentReadyMessageError(str(error)) from error

        return await self._content_indexer.index(
            RagDocumentContent(
                resource_id=message.resource_id,
                document_version=message.version,
                markdown=message.content,
            )
        )


class RagAclRecalculateConsumer:
    """消费 ACL 刷新事件，并同步检索侧权限投影。"""

    __slots__ = ("_projection_targets", "_repository")

    def __init__(
            self,
            *,
            repository: RagAclProjectionRepository,
            projection_targets: Sequence[RagAclProjectionTarget],
    ) -> None:
        self._repository = repository
        self._projection_targets = tuple(projection_targets)

    async def handle(self, payload: Mapping[str, Any]) -> None:
        try:
            message = _AclRecalculateMessage.model_validate(payload)
        except ValidationError as error:
            raise RagAclProjectionError(str(error)) from error

        projection = await self.refresh(message)

        if projection is None:
            warn(
                "rag acl refresh skipped because resource was not found.",
                resource_id=message.resource_id,
            )
            return

        info(
            "rag acl projection refreshed.",
            resource_id=projection.resource_id,
        )

    async def refresh(self, message: _AclRecalculateMessage) -> RagResourceAclProjection | None:
        """读取权威 ACL 并刷新本地投影。
        
        一次 ACL 重算事件触发：
        load_authoritative_projection 从 Java 服务端读取最新 ACL 投影
        -> upsert_projection 更新本地 ACL 投影
        -> update_acl_projection 同步到检索后端
        """
        projection = await self._repository.load_authoritative_projection(message.resource_id)
        if projection is None:
            return None

        if projection.resource_id != message.resource_id:
            raise RagAclProjectionError("ACL projection resourceId does not match recalculate message.")

        await self._repository.upsert_projection(projection)

        # 同步 Qdrant, Neo4j 等检索后端中的 ACL 投影。
        for target in self._projection_targets:
            await target.update_acl_projection(projection)

        return projection


class RagResourceDeletedConsumer:
    """消费资源删除事实，并清理 RAG 派生数据。"""

    __slots__ = ("_targets",)

    def __init__(self, *, targets: Sequence[RagResourceDeletionTarget]) -> None:
        self._targets = tuple(targets)

    async def handle(self, payload: Mapping[str, Any]) -> None:
        try:
            message = _ResourceDeletedMessage.model_validate(payload)
        except ValidationError as error:
            raise RagResourceDeletionError(str(error)) from error

        if not message.resource_ids:
            return

        for target in self._targets:
            await target.delete_resources(message.resource_ids)

        info(
            "rag resources deleted.",
            resource_count=len(message.resource_ids),
        )
