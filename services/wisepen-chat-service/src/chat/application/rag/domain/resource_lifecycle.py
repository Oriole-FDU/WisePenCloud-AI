from dataclasses import dataclass
from typing import Optional

from .index_publication import VersionSnapshot
from ..enums import ResourceKind


@dataclass(frozen=True, slots=True)
class RagResource:
    """RAG 资源元数据。

    描述一个可索引的资源实体（笔记 / 文档），
    包含内容、版本、标题和软删除状态。
    """

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    content: str
    version: int = 1
    title: Optional[str] = None
    document_name: Optional[str] = None
    is_deleted: bool = False

    @property
    def display_name(self) -> str:
        """获取资源的可读展示名称。

        笔记优先使用 title，文档优先使用 document_name，
        兜底使用 resource_id。
        """
        if self.resource_kind == ResourceKind.NOTE and self.title:
            return self.title
        if self.resource_kind == ResourceKind.DOCUMENT and self.document_name:
            return self.document_name
        return self.resource_id


@dataclass(frozen=True, slots=True)
class ResourceUpsertResult:
    """资源写入结果。

    包含写入后的资源对象及其对应的版本快照。
    """

    resource: RagResource
    version_snapshot: VersionSnapshot
