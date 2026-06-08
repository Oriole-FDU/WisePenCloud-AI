from datetime import datetime, timezone
from typing import Any, Dict, Optional

from beanie import Document
from pydantic import ConfigDict, Field
from pymongo import ASCENDING, IndexModel

from chat.application.rag.enums import ResourceKind
from chat.application.rag.permissions import (
    acl_projection_from_dict,
    acl_projection_to_dict,
    build_owner_acl_projection,
)
from chat.application.rag.runtime.enums import RagIndexingStatus
from chat.application.rag.runtime.models import (
    RagResource,
)


class NoteResourceDocument(Document):

    resource_id: str = Field(..., description="资源唯一标识")
    user_id: str = Field(..., description="用户 ID")
    content: str = Field(..., description="笔记原文内容")
    title: Optional[str] = Field(default=None, description="笔记标题")
    acl_projection: Dict[str, Any] = Field(..., description="RAG 本地 ACL 投影")

    version: int = Field(default=1, description="资源事实版本")
    is_deleted: bool = Field(default=False, description="是否逻辑删除")
    indexing_status: RagIndexingStatus = Field(..., description="索引生命周期状态")
    indexing_error: Optional[str] = Field(default=None, description="最近一次索引错误")
    last_index_version: Optional[str] = Field(default=None, description="最近一次索引版本")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(frozen=False)

    class Settings:
        name = "rag_notes"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("resource_id", ASCENDING)],
                unique=True,
                name="uq_user_resource",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("is_deleted", ASCENDING)],
                name="idx_user_deleted",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("indexing_status", ASCENDING)],
                name="idx_user_indexing_status",
            ),
        ]

    def to_domain(self) -> RagResource:
        return RagResource(
            resource_id=self.resource_id,
            user_id=self.user_id,
            resource_kind=ResourceKind.NOTE,
            content=self.content,
            title=self.title,
            document_name=None,
            version=self.version,
            is_deleted=self.is_deleted,
            indexing_status=self.indexing_status,
            indexing_error=self.indexing_error,
            last_index_version=self.last_index_version,
            acl_projection=acl_projection_from_dict(self.acl_projection),
        )


class DocumentResourceDocument(Document):

    resource_id: str = Field(..., description="资源唯一标识")
    user_id: str = Field(..., description="用户 ID")
    content: str = Field(..., description="文档原文内容")
    document_name: Optional[str] = Field(default=None, description="文档名")
    acl_projection: Dict[str, Any] = Field(..., description="RAG 本地 ACL 投影")

    version: int = Field(default=1, description="资源事实版本")
    is_deleted: bool = Field(default=False, description="是否逻辑删除")
    indexing_status: RagIndexingStatus = Field(..., description="索引生命周期状态")
    indexing_error: Optional[str] = Field(default=None, description="最近一次索引错误")
    last_index_version: Optional[str] = Field(default=None, description="最近一次索引版本")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(frozen=False)

    class Settings:
        name = "rag_documents"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("resource_id", ASCENDING)],
                unique=True,
                name="uq_user_resource",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("is_deleted", ASCENDING)],
                name="idx_user_deleted",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("indexing_status", ASCENDING)],
                name="idx_user_indexing_status",
            ),
        ]

    def to_domain(self) -> RagResource:
        return RagResource(
            resource_id=self.resource_id,
            user_id=self.user_id,
            resource_kind=ResourceKind.DOCUMENT,
            content=self.content,
            title=None,
            document_name=self.document_name,
            version=self.version,
            is_deleted=self.is_deleted,
            indexing_status=self.indexing_status,
            indexing_error=self.indexing_error,
            last_index_version=self.last_index_version,
            acl_projection=acl_projection_from_dict(self.acl_projection),
        )


def build_resource_acl_document(resource: RagResource) -> Dict[str, Any]:
    """构造资源表持久化用 ACL 字段。"""
    projection = (
        resource.acl_projection
        if resource.acl_projection is not None
        else build_owner_acl_projection(resource.user_id)
    )
    return acl_projection_to_dict(projection)
