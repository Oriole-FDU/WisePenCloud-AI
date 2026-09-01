"""只读取 active revision 且通过 ACL 的文档和全局 Section。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from common.utils.document import Section

from rag_v3.application.document.models import Document
from rag_v3.domain.acl import PermissionScope
from rag_v3.domain.repositories.acl import ResourceAclRepository
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.index_state import ResourceIndexStateRepository


@dataclass(frozen=True, slots=True)
class ResolvedSection:
    """一个已确认 active 且当前用户可读的全局 Section 定位结果。"""

    document: Document
    section: Section


class ActiveDocumentSnapshotLoader:
    """为读取、标题树提供统一的 active+ACL 前置。"""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        index_states: ResourceIndexStateRepository,
        resource_acls: ResourceAclRepository,
    ) -> None:
        self._documents = documents
        self._index_states = index_states
        self._resource_acls = resource_acls

    async def load_documents(
        self,
        resource_ids: Sequence[str],
        *,
        scope: PermissionScope,
    ) -> Mapping[str, Document]:
        """按输入资源批量读取当前可见版本；ACL 缺失时拒绝。"""
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}
        states = await self._index_states.get_states(unique_resource_ids)
        active_revisions = [
            (resource_id, state.applied_content_revision)
            for resource_id, state in states.items()
            if state.applied_content_revision is not None
        ]
        documents = await self._documents.get_revisions(active_revisions)
        resource_acls = await self._resource_acls.get_resource_acls(
            [resource_id for resource_id, _ in active_revisions]
        )

        result: dict[str, Document] = {}
        for resource_id in unique_resource_ids:
            state = states.get(resource_id)
            if state is None or state.applied_content_revision is None:
                continue
            resource_acl = resource_acls.get(resource_id)
            if resource_acl is None or not resource_acl.can_read(scope):
                continue
            document = documents.get((resource_id, state.applied_content_revision))
            if document is not None:
                result[resource_id] = document
        return result

    async def locate_sections(
        self,
        section_ids: Sequence[str],
        *,
        scope: PermissionScope,
    ) -> Mapping[str, ResolvedSection]:
        """定位一批全局 Section ID；不存在和无权条目都不返回。"""
        unique_section_ids = list(dict.fromkeys(section_ids))
        if not unique_section_ids:
            return {}

        # 查询可能命中未清理旧 revision，必须在下方以 active 指针重新过滤。
        candidates = await self._documents.find_by_section_ids(unique_section_ids)
        candidate_resource_ids = list(
            dict.fromkeys(document.resource_id for document in candidates)
        )
        states = await self._index_states.get_states(candidate_resource_ids)
        resource_acls = await self._resource_acls.get_resource_acls(
            candidate_resource_ids
        )

        active_documents: dict[str, Document] = {}
        for document in candidates:
            state = states.get(document.resource_id)
            resource_acl = resource_acls.get(document.resource_id)
            if state is None or state.applied_content_revision != document.revision.content_revision:
                continue
            if resource_acl is None or not resource_acl.can_read(scope):
                continue
            active_documents[document.resource_id] = document

        locations: dict[str, ResolvedSection] = {}
        requested = set(unique_section_ids)
        for document in active_documents.values():
            for section in document.structure.sections:
                if section.section_id in requested:
                    locations[section.section_id] = ResolvedSection(
                        document=document,
                        section=section,
                    )
        return locations
