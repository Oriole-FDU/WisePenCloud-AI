from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import ConfigDict, Field
from pymongo import ASCENDING, IndexModel

from chat.application.rag.domain.resource_lifecycle import RagResource
from chat.application.rag.enums import ResourceKind


class NoteResourceDocument(Document):

    resource_id: str = Field(..., description="资源唯一标识")
    user_id: str = Field(..., description="用户 ID")
    content: str = Field(..., description="笔记原文内容")
    title: Optional[str] = Field(default=None, description="笔记标题")

    version: int = Field(default=1, description="资源事实版本")
    is_deleted: bool = Field(default=False, description="是否逻辑删除")

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
        )


class DocumentResourceDocument(Document):

    resource_id: str = Field(..., description="资源唯一标识")
    user_id: str = Field(..., description="用户 ID")
    content: str = Field(..., description="文档原文内容")
    document_name: Optional[str] = Field(default=None, description="文档名")

    version: int = Field(default=1, description="资源事实版本")
    is_deleted: bool = Field(default=False, description="是否逻辑删除")

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
        )