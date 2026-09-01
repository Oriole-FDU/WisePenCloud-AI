"""查询前批量定位 active Document，并建立一次 ACL 快照。"""

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
    """定位到的文档与所属 section 组合。"""
    document: Document
    section: Section


# --- Active 文档与 ACL 快照加载器 ---

class ActiveDocumentSnapshotLoader:
    """为读取、标题树和其他查询用例提供最小 active+ACL 前置。"""

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
        """根据资源 ID 批量加载当前 active 且用户可读的文档。"""
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}

        # 获取各资源的发布状态
        states = await self._index_states.get_states(unique_resource_ids)

        # 收集已发布的有效 revision
        active_revisions = [
            (resource_id, state.applied_content_revision)
            for resource_id, state in states.items()
            if state.applied_content_revision is not None
        ]

        # 并行获取文档内容和 ACL
        documents = await self._documents.get_revisions(active_revisions)
        resource_acls = await self._resource_acls.get_resource_acls(
            [resource_id for resource_id, _ in active_revisions]
        )

        # 逐个校验 ACL 权限和文档存在性
        result: dict[str, Document] = {}
        for resource_id in unique_resource_ids:
            state = states.get(resource_id)
            if state is None or state.applied_content_revision is None:
                continue
            acl = resource_acls.get(resource_id)
            if acl is None or not acl.can_read(scope):
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
        """根据 section ID 定位到对应的文档和 section 对象，并校验 active + 权限。"""
        unique_section_ids = list(dict.fromkeys(section_ids))
        if not unique_section_ids:
            return {}

        # 先通过 section ID 反查可能所属的文档
        candidates = await self._documents.find_by_section_ids(unique_section_ids)
        resource_ids = list(dict.fromkeys(doc.resource_id for doc in candidates))

        # 获取发布状态和 ACL
        states = await self._index_states.get_states(resource_ids)
        acls = await self._resource_acls.get_resource_acls(resource_ids)

        locations: dict[str, ResolvedSection] = {}
        requested = set(unique_section_ids)

        for document in candidates:
            state = states.get(document.resource_id)
            acl = acls.get(document.resource_id)
            # 检查文档是否已发布、revision 一致、且用户可读
            if (
                state is None
                or state.applied_content_revision != document.revision.content_revision
                or acl is None
                or not acl.can_read(scope)
            ):
                continue

            # 遍历文档的 section，匹配请求的 section_id
            for section in document.structure.sections:
                if section.section_id in requested:
                    locations[section.section_id] = ResolvedSection(document, section)

        return locations