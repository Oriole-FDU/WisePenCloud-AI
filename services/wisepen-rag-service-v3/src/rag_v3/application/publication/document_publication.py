"""P0 文档 revision 的事实写入和 active 发布编排。"""

from rag_v3.domain.models import ContentRevision, Document
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.index_state import (
    ResourceIndexStateRepository,
    StageAction,
)


class DocumentPublication:
    """协调文档事实与 active 指针，不承担索引或图谱构建。"""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        index_states: ResourceIndexStateRepository,
    ) -> None:
        self._documents = documents
        self._index_states = index_states

    async def stage_document(self, document: Document) -> StageAction:
        """暂存 revision 后保存权威文档事实；旧版本不会写入正文。"""
        states = await self._index_states.get_states([document.resource_id])
        current = states.get(document.resource_id)
        current_revision = (
            current.applied_content_revision if current is not None else None
        )
        if current_revision is not None:
            active_documents = await self._documents.get_revisions(
                [(document.resource_id, current_revision)]
            )
            active_document = active_documents.get(
                (document.resource_id, current_revision)
            )
            if (
                active_document is not None
                and active_document.revision.document_version
                > document.revision.document_version
            ):
                return StageAction.STALE

        # 条件更新固定刚观察到的 active revision；并发变更不能被旧任务覆盖。
        action = await self._index_states.stage_revision(
            document.revision,
            expected_applied_content_revision=current_revision,
        )
        if action is StageAction.STALE:
            return action
        await self._documents.save_revision(document)
        return action

    async def apply_revision(self, revision: ContentRevision) -> None:
        """仅当同一 revision 已有权威 Document 事实时才允许对外可见。"""
        if not await self._documents.exists(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
        ):
            raise ValueError(
                f"document revision {revision.content_revision} is not persisted"
            )
        await self._index_states.apply_revision(revision)

    async def clear_resources(self, resource_ids: list[str]) -> None:
        """先撤销可见性；物理清理由后续后台用例处理。"""
        await self._index_states.clear_visibility(resource_ids)
