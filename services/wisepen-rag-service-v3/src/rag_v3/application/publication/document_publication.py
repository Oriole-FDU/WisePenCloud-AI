"""P0 文档 revision 的事实写入和 active 发布编排。"""

from rag_v3.domain.models import ContentRevision, Document
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.document_vectors import DocumentVectorRepository
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
        doc_chunks: DocChunkRepository,
        document_vectors: DocumentVectorRepository,
        index_states: ResourceIndexStateRepository,
    ) -> None:
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._document_vectors = document_vectors
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
        """仅在 Mongo 事实和文档向量投影完整时允许对外可见。"""
        documents = await self._documents.get_revisions(
            [(revision.resource_id, revision.content_revision)]
        )
        document = documents.get((revision.resource_id, revision.content_revision))
        if document is None:
            raise ValueError(
                f"document revision {revision.content_revision} is not persisted"
            )
        chunks = await self._doc_chunks.get_revision_chunks(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
        )
        if document.raw_content.strip() and not chunks:
            raise ValueError(
                f"document chunks for {revision.content_revision} are not persisted"
            )
        if not await self._document_vectors.is_complete(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
            chunk_ids=[chunk.chunk_id for chunk in chunks],
        ):
            raise ValueError(
                f"document vector revision {revision.content_revision} is incomplete"
            )
        await self._index_states.apply_revision(revision)

    async def clear_resources(self, resource_ids: list[str]) -> None:
        """先撤销可见性；物理清理由后续后台用例处理。"""
        await self._index_states.clear_visibility(resource_ids)
