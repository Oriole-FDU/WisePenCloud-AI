"""P0 application 测试使用的内存仓储与构造型 Document。"""

from collections.abc import Mapping, Sequence

from common.utils.document import Section, SourceSpan

from rag.application.document.models import (
    ContentRevision,
    DocChunk,
    Document,
    DocumentStructure,
    ResourceIndexState,
    rag_chunk_id,
)
from rag.domain.acl import ResourceAcl
from rag.domain.repositories.index_state import StageAction


class MemoryDocuments:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], Document] = {}
        self.get_revision_calls = 0
        self.section_lookup_calls = 0

    async def save_revision(self, document: Document) -> None:
        existing = self.documents.get(
            (document.resource_id, document.revision.content_revision)
        )
        if existing is not None and existing.metadata != document.metadata:
            raise ValueError("document metadata differs for the same content revision")
        self.documents[(document.resource_id, document.revision.content_revision)] = document

    async def exists(self, *, resource_id: str, content_revision: str) -> bool:
        return (resource_id, content_revision) in self.documents

    async def get_revisions(
        self,
        revisions: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], Document]:
        self.get_revision_calls += 1
        return {
            revision: self.documents[revision]
            for revision in revisions
            if revision in self.documents
        }

    async def find_by_section_ids(self, section_ids: Sequence[str]) -> list[Document]:
        self.section_lookup_calls += 1
        wanted = set(section_ids)
        return [
            document
            for document in self.documents.values()
            if any(section.section_id in wanted for section in document.structure.sections)
        ]


class MemoryDocChunks:
    """DocumentPreparer 测试使用的 revision 级幂等 Chunk 仓储。"""

    def __init__(self) -> None:
        self.chunks: dict[str, DocChunk] = {}
        self.save_calls = 0

    async def save_revision(self, chunks: Sequence[DocChunk]) -> None:
        self.save_calls += 1
        for chunk in chunks:
            self.chunks[chunk.chunk_id] = chunk

    async def get_revision_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> list[DocChunk]:
        return sorted(
            (
                chunk
                for chunk in self.chunks.values()
                if chunk.resource_id == resource_id
                and chunk.content_revision == content_revision
            ),
            key=lambda chunk: chunk.chunk_index,
        )

    async def get_section_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
        section_ids: Sequence[str],
    ) -> list[DocChunk]:
        wanted = set(section_ids)
        return [
            chunk
            for chunk in await self.get_revision_chunks(
                resource_id=resource_id,
                content_revision=content_revision,
            )
            if chunk.section_id in wanted
        ]

    async def get_chunks_by_ids(self, chunk_ids: Sequence[str]) -> list[DocChunk]:
        wanted = set(chunk_ids)
        return [chunk for chunk in self.chunks.values() if chunk.chunk_id in wanted]

    async def get_revisions_chunks(
        self,
        revisions: Sequence[tuple[str, str]],
    ) -> list[DocChunk]:
        wanted = set(revisions)
        return sorted(
            (
                chunk
                for chunk in self.chunks.values()
                if (chunk.resource_id, chunk.content_revision) in wanted
            ),
            key=lambda chunk: (chunk.resource_id, chunk.content_revision, chunk.chunk_index),
        )


class MemoryDocumentVectors:
    """P1-B 测试使用的文档向量投影替身。"""

    def __init__(self) -> None:
        self.revision_chunk_ids: dict[tuple[str, str], set[str]] = {}
        self.write_calls = 0

    async def write_revision(self, *, chunks, dense_vectors, resource_acl) -> None:
        self.write_calls += 1
        if chunks:
            key = (chunks[0].resource_id, chunks[0].content_revision)
            self.revision_chunk_ids[key] = {chunk.chunk_id for chunk in chunks}

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        chunk_ids,
    ) -> bool:
        return self.revision_chunk_ids.get(
            (resource_id, content_revision), set()
        ) == set(chunk_ids)


class MemoryIndexStates:
    def __init__(self) -> None:
        self.states: dict[str, ResourceIndexState] = {}
        self.get_states_calls = 0

    async def stage_revision(
        self,
        revision: ContentRevision,
        *,
        expected_applied_content_revision: str | None,
    ) -> StageAction:
        current = self.states.get(revision.resource_id)
        current_revision = (
            current.applied_content_revision if current is not None else None
        )
        if current_revision != expected_applied_content_revision:
            raise RuntimeError("active revision changed concurrently")
        if current is not None and current.applied_content_revision == revision.content_revision:
            return StageAction.ALREADY_APPLIED
        if current is not None and current.staged_document_version is not None and current.staged_document_version > revision.document_version:
            return StageAction.STALE
        self.states[revision.resource_id] = ResourceIndexState(
            resource_id=revision.resource_id,
            staged_content_revision=revision.content_revision,
            staged_document_version=revision.document_version,
            applied_content_revision=(
                current.applied_content_revision if current is not None else None
            ),
        )
        return StageAction.STAGED

    async def apply_revision(self, revision: ContentRevision) -> None:
        current = self.states.get(revision.resource_id)
        if (
            current is None
            or current.staged_content_revision != revision.content_revision
            or current.staged_document_version != revision.document_version
        ):
            if current is not None and current.applied_content_revision == revision.content_revision:
                return
            raise RuntimeError("revision is not staged")
        self.states[revision.resource_id] = ResourceIndexState(
            resource_id=revision.resource_id,
            applied_content_revision=revision.content_revision,
        )

    async def get_states(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceIndexState]:
        self.get_states_calls += 1
        return {
            resource_id: self.states[resource_id]
            for resource_id in resource_ids
            if resource_id in self.states
        }

    async def clear_visibility(self, resource_ids: Sequence[str]) -> None:
        for resource_id in resource_ids:
            if resource_id in self.states:
                self.states[resource_id] = ResourceIndexState(resource_id=resource_id)


class MemoryAcls:
    def __init__(self, values: Mapping[str, ResourceAcl]) -> None:
        self.values = dict(values)
        self.get_acls_calls = 0

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]:
        self.get_acls_calls += 1
        return {
            resource_id: self.values[resource_id]
            for resource_id in resource_ids
            if resource_id in self.values
        }

    async def save_if_newer(self, resource_acl: ResourceAcl) -> bool:
        current = self.values.get(resource_acl.resource_id)
        if current is not None and current.acl_revision > resource_acl.acl_revision:
            return False
        self.values[resource_acl.resource_id] = resource_acl
        return True


def document(
    *,
    resource_id: str,
    version: int,
    section_id: str,
    raw_content: str = "content",
) -> Document:
    revision = ContentRevision.create(
        resource_id=resource_id,
        document_version=version,
        raw_content=raw_content,
    )
    section = Section(
        section_id=section_id,
        title="Title",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=("Title",),
        own_span=SourceSpan(0, len(raw_content)),
        subtree_span=SourceSpan(0, len(raw_content)),
        content_spans=[SourceSpan(0, len(raw_content))],
        preview=raw_content,
    )
    return Document(
        resource_id=resource_id,
        revision=revision,
        raw_content=raw_content,
        structure=DocumentStructure(total_length=len(raw_content), sections=(section,)),
    )


def chunk_for_document(document: Document) -> DocChunk:
    """为 P0 发布测试补齐 P1 后必需的最小 Chunk 投影。"""
    return DocChunk(
        chunk_id=rag_chunk_id(
            resource_id=document.resource_id,
            content_revision=document.revision.content_revision,
            common_chunk_id="test-chunk",
        ),
        resource_id=document.resource_id,
        content_revision=document.revision.content_revision,
        chunk_index=0,
        section_id=(
            document.structure.sections[0].section_id
            if document.structure.sections
            else None
        ),
        section_path=(
            document.structure.sections[0].section_path
            if document.structure.sections
            else ()
        ),
        raw_text=document.raw_content,
        source_spans=(SourceSpan(0, len(document.raw_content)),),
    )
