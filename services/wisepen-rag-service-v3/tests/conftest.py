"""P0 application 测试使用的内存仓储与构造型 Document。"""

from collections.abc import Mapping, Sequence

from common.utils.document import Section, SourceSpan

from rag_v3.domain.acl import ResourceAcl
from rag_v3.domain.models import (
    ContentRevision,
    Document,
    DocumentStructure,
    ResourceIndexState,
)
from rag_v3.domain.repositories.index_state import StageAction


class MemoryDocuments:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], Document] = {}
        self.get_revision_calls = 0
        self.section_lookup_calls = 0

    async def save_revision(self, document: Document) -> None:
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
