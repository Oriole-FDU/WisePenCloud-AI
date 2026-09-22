"""RAG 本地 ACL Mongo 实体。"""

from typing import ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


class GroupResourceAclEntity(BaseModel):
    group_id: str
    default_readable: bool
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)


class ResourceAclEntity(Document):
    """来自上游的本地 ACL 投影，服务在线 fail-closed 判权。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)
    group_acls: list[GroupResourceAclEntity] = Field(default_factory=list)

    class Settings:
        name = "resource_acls"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING)],
                name="resource_acl_resource_unique",
                unique=True,
            ),
        ]
