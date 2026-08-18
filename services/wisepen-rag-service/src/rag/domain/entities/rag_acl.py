"""RAG 本地资源 ACL 的 Beanie 持久化实体。"""

from typing import ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


class GroupResourceAcl(BaseModel):
    group_id: str
    is_readable: bool
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)


class ResourceAclEntity(Document):
    """在线授权和后端同步共同消费的本地 ACL 事实。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)
    group_acls: list[GroupResourceAcl] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_v2_resource_acls"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING)],
                name="idx_rag_v2_resource_acl_resource",
                unique=True,
            ),
        ]
