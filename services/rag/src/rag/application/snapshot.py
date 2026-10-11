"""一次调用内的资源版本与权限快照，以及供 application 用例共享的内容读取入口。"""

from dataclasses import dataclass, field

from rag.acl.models import PermissionScope
from rag.acl.store import ResourceAclRepository
from rag.application.publication.store import PublicationStore
from rag.core.schema import Chunk, ContentRevision, Document
from rag.storage.content import ContentReader


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """逐资源保存本次调用首次确定的版本与可见性，不表示跨后端原子观察点。"""

    scope: PermissionScope  # 来自可信入口，同一快照不更换用户身份。
    _revisions: dict[str, ContentRevision | None] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )  # None 缓存不可见决定；不保存正文或额外拒绝原因。


class ResourceSnapshotReader:
    """共享读取服务只保存固定依赖；请求决定显式传入并保存在 snapshot。"""

    def __init__(
        self,
        *,
        store: PublicationStore,
        resource_acls: ResourceAclRepository,
        reader: ContentReader,
    ) -> None:
        self._store = store
        self._resource_acls = resource_acls
        self._reader = reader

    async def read_documents(
        self, resource_ids: list[str], *, snapshot: ResourceSnapshot
    ) -> list[Document]:
        """先授权并固定版本，再批量读取；身份不符或缺失只影响该对象。"""
        revisions = await self._resolve(resource_ids, snapshot=snapshot)
        if not revisions:
            return []
        documents = await self._reader.get_documents(
            [
                (resource_id, revision.content_revision)
                for resource_id, revision in revisions.items()
            ]
        )
        return _visible_documents(documents, revisions)

    async def read_documents_by_section_ids(
        self, section_ids: list[str], *, snapshot: ResourceSnapshot
    ) -> list[Document]:
        """先批量定位，再按首次版本与资源授权过滤；内部正文不提前交给用例。"""
        unique_ids = list(dict.fromkeys(section_ids))
        if not unique_ids:
            return []
        documents = await self._reader.get_documents_by_section_ids(unique_ids)
        revisions = await self._resolve(
            [document.resource_id for document in documents], snapshot=snapshot
        )
        # 只核对定位到的事实，不换读新版；旧章节 ID 不能成为新版正文入口。
        return _visible_documents(documents, revisions)

    async def read_chunks(
        self, chunk_ids: list[str], *, snapshot: ResourceSnapshot
    ) -> list[Chunk]:
        """批量定位所属资源，Chunk 继承 Document 级授权并匹配已选 revision。"""
        if not chunk_ids:
            return []
        chunks = await self._reader.get_chunks(chunk_ids)
        revisions = await self._resolve(
            [chunk.resource_id for chunk in chunks], snapshot=snapshot
        )
        # 定位取得的正文只在此内部流转；通过资源授权与版本检查后才能交给调用方。
        return [
            chunk
            for chunk in chunks
            if chunk.resource_id in revisions
            and chunk.content_revision == revisions[chunk.resource_id].content_revision
        ]

    async def _resolve(
        self, resource_ids: list[str], *, snapshot: ResourceSnapshot
    ) -> dict[str, ContentRevision]:
        """只解析尚未观察的资源；不可见也缓存，I/O 失败不写入决定。"""
        unique_ids = list(dict.fromkeys(resource_ids))
        pending = [
            resource_id
            for resource_id in unique_ids
            if resource_id not in snapshot._revisions
        ]
        if pending:
            states = await self._store.get_states(pending)
            applied = {
                state.resource_id: state.applied_revision
                for state in states
                if not state.destroyed and state.applied_revision is not None
            }
            acls = (
                await self._resource_acls.get_resource_acls(list(applied))
                if applied
                else {}
            )
            # 两次 I/O 完成后才缓存决定，基础设施失败不能变成“已观察不可见”。
            for resource_id in pending:
                revision = applied.get(resource_id)
                acl = acls.get(resource_id)
                # 子操作交错解析时也保留已完成的首次决定，不覆盖已有 revision。
                snapshot._revisions.setdefault(
                    resource_id,
                    revision
                    if revision is not None
                    and acl is not None
                    and acl.can_read(snapshot.scope)
                    else None,
                )

        return {
            resource_id: snapshot._revisions[resource_id]
            for resource_id in unique_ids
            if snapshot._revisions[resource_id] is not None
        }


def _visible_documents(
    documents: list[Document], revisions: dict[str, ContentRevision]
) -> list[Document]:
    """所有 Document 读取路径共用完整正文身份检查。"""
    visible = []
    for document in documents:
        selected = revisions.get(document.resource_id)
        if selected is None or document.content_revision != selected.content_revision:
            continue
        # 不能仅凭持久化的 revision 标签信任正文。
        actual = ContentRevision.create(
            resource_id=document.resource_id,
            document_version=document.document_version,
            text=document.text,
        )
        if actual == selected:
            visible.append(document)
    return visible
