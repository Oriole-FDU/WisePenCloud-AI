"""资源事件到可检索状态的发布用例：校验、摄入、事实回查及条件发布。"""

import asyncio
from dataclasses import replace
from typing import Annotated

from common.utils.markdown import MarkdownChunker
from pydantic import BaseModel, ConfigDict, Field

from rag.application.publication.models import (
    ContentIdentityError,
    ContentIncompleteError,
    PublicationAction,
)
from rag.application.publication.store import PublicationStore
from rag.backends.kafka import KafkaConsumerClient, RetryPolicy
from rag.core.schema import ContentRevision, Document, DocumentStructure
from rag.core.schema.content import rag_section_id
from rag.ingestion.pipeline import IngestionPipeline
from rag.storage.content import ContentReader, ContentWriter


class DocumentPublication:
    """发布完整文档或永久销毁资源；调用方提供完整 Pipeline 并串行同资源构建。"""

    def __init__(
        self,
        *,
        store: PublicationStore,
        reader: ContentReader,
        writer: ContentWriter,
        pipeline: IngestionPipeline,
    ) -> None:
        self._store = store
        self._reader = reader
        self._writer = writer
        # 外部固定事实 Indexer 与全部必需投影顺序；用例不检查 Pipeline 的组件
        self._pipeline = pipeline

    async def publish(self, document: Document) -> PublicationAction:
        """核对身份并摄入，再回查事实和条件发布；重复、旧版本及销毁返回跳过结果。

        必需投影完整性依赖 Pipeline 的 Indexer 成功合同。失败直接传播，保留旧
        applied 与已完成的事实和投影；回查不代替向量确认或跨数据库事务。
        """
        revision = ContentRevision.create(
            resource_id=document.resource_id,
            document_version=document.document_version,
            text=document.text,
        )
        if document.content_revision != revision.content_revision:
            raise ContentIdentityError(
                f"document {document.resource_id} content_revision does not match its text"
            )

        # Store 原子判断版本、冲突与销毁状态，只有仍需构建的候选进入摄入
        action = await self._store.stage_revision(revision)
        if action is not PublicationAction.STAGED:
            return action

        await self._writer.upsert_documents([document])
        expected_chunks = await self._pipeline.run(document)

        documents = await self._reader.get_documents(
            [(revision.resource_id, revision.content_revision)]
        )
        if not documents:
            raise ContentIncompleteError(
                f"document {revision.content_revision} is missing"
            )
        persisted = documents[0]
        persisted_revision = ContentRevision.create(
            resource_id=persisted.resource_id,
            document_version=persisted.document_version,
            text=persisted.text,
        )
        if (
            persisted_revision != revision
            or persisted.content_revision != revision.content_revision
        ):
            raise ContentIncompleteError(
                f"document {revision.content_revision} identity does not match"
            )

        chunks = await self._reader.get_revision_chunks(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
        )
        # 预期集合来自本次成功 Pipeline，不能从已落库的子集反推“完整”
        if [chunk.chunk_id for chunk in chunks] != [
            chunk.chunk_id for chunk in expected_chunks
        ]:
            raise ContentIncompleteError(
                f"chunks for {revision.content_revision} do not match the expected set"
            )
        # 构建或回查期间仍可被更高版本或销毁取代；最终可见性由条件 apply 决定
        return await self._store.apply_revision(revision)

    async def destroy_resources(self, resource_ids: list[str]) -> None:
        """建立永久销毁标记，阻断迟到内容的首次摄入路径。"""
        await self._store.destroy_resources(resource_ids)


class DocumentReadyEvent(BaseModel):
    """对应 DocumentReadyMessage，仅消费资源身份、版本与完整 Markdown。"""

    model_config = ConfigDict(strict=True, extra="ignore")

    resource_id: str = Field(alias="resourceId", min_length=1)
    document_version: int = Field(alias="version")
    content: str  # 空白也是正文事实，允许零 Chunk 文档

    def to_document(self) -> Document:
        """构造领域文档；结构复用 Common MarkdownChunker，不重写章节与锚点算法。"""
        revision = ContentRevision.create(
            resource_id=self.resource_id,
            document_version=self.document_version,
            text=self.content,
        )
        parsed = MarkdownChunker().chunk(self.content)
        # 只重写身份与父引用，Common 的树、原文范围与预览仍是结构事实。
        section_ids = {
            section.section_id: rag_section_id(
                resource_id=self.resource_id,
                content_revision=revision.content_revision,
                common_section_id=section.section_id,
            )
            for section in parsed.sections
        }
        sections = [
            replace(
                section,
                section_id=section_ids[section.section_id],
                parent_section_id=(
                    section_ids[section.parent_section_id]
                    if section.parent_section_id is not None
                    else None
                ),
            )
            for section in parsed.sections
        ]
        return Document(
            resource_id=self.resource_id,
            document_version=self.document_version,
            content_revision=revision.content_revision,
            text=self.content,
            structure=DocumentStructure(len(self.content), sections, parsed.anchors),
        )


class ResourceDestroyedEvent(BaseModel):
    """对应 ResourceDeletedMessage；所有资源类型均建立永久销毁标记。"""

    model_config = ConfigDict(strict=True, extra="ignore")

    typed_resource_ids: dict[str, list[Annotated[str, Field(min_length=1)]]] = Field(
        alias="typedResourceIds"
    )

    @property
    def resource_ids(self) -> list[str]:
        """展开上游类型分组；去重与批量更新由状态端口负责。"""
        return [
            resource_id
            for resource_ids in self.typed_resource_ids.values()
            for resource_id in resource_ids
        ]


class PublicationEventConsumer:
    """管理事件消费；业务失败按策略重试或进 DLQ，基础设施失败交由入口重启。"""

    def __init__(
        self,
        *,
        publisher: DocumentPublication,
        bootstrap_servers: str,
        group_id: str,
        document_ready_topic: str = "wisepen-document-ready-topic",
        physical_destroy_topic: str = "wisepen-resource-physical-destroy-topic",
        max_poll_interval_ms: int = 300_000,
        content_retry_policy: RetryPolicy | None = None,
        destroy_retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._publisher = publisher
        self._content_consumer = KafkaConsumerClient(
            bootstrap_servers=bootstrap_servers,
            topic=document_ready_topic,
            group_id=group_id,
            handler=self._handle_document_ready,
            max_poll_interval_ms=max_poll_interval_ms,
            retry_policy=content_retry_policy,
            is_retryable=_is_retryable,
        )
        self._destroy_consumer = KafkaConsumerClient(
            bootstrap_servers=bootstrap_servers,
            topic=physical_destroy_topic,
            group_id=group_id,
            handler=self._handle_resource_destroyed,
            max_poll_interval_ms=max_poll_interval_ms,
            retry_policy=destroy_retry_policy,
            is_retryable=_is_retryable,
        )

    async def run(self) -> None:
        """前台运行到失败或取消；运行入口须 await 此协程并观察异常。

        首次无已提交 offset 时从最早记录开始。内容与销毁不依赖跨 topic 顺序；
        内容循环逐条执行，上游须继续以 resourceId 作为内容事件的 Kafka key。
        销毁独立消费，即使普通构建正在等待 I/O，也能撤销候选可见性。
        """
        tasks = [
            asyncio.create_task(
                self._content_consumer.run(), name="publication-content"
            ),
            asyncio.create_task(
                self._destroy_consumer.run(), name="publication-destroy"
            ),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            # 任一循环退出后取消另一个并取走其异常，避免遗留后台消费任务
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_document_ready(self, payload: dict[str, object]) -> None:
        event = DocumentReadyEvent.model_validate(payload)
        # applied 与明确跳过均正常返回；失败交由 Kafka 重试或 DLQ 时序承接
        await self._publisher.publish(event.to_document())

    async def _handle_resource_destroyed(self, payload: dict[str, object]) -> None:
        event = ResourceDestroyedEvent.model_validate(payload)
        await self._publisher.destroy_resources(event.resource_ids)


def _is_retryable(error: Exception) -> bool:
    """事实不完整可重试；身份冲突与输入错误属永久失败。"""
    return isinstance(error, ContentIncompleteError) or not isinstance(
        error, ValueError
    )
