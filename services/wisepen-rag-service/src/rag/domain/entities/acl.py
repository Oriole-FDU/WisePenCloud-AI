"""资源 ACL Mongo 实体。"""

from beanie import Document as BeanieDocument
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from rag.domain.models.acl import GroupResourceAcl


class ResourceAclEntity(BeanieDocument):
    """resource_acls 集合中的授权投影。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)
    group_acls: list[GroupResourceAcl] = Field(default_factory=list)

    class Settings:
        name = "resource_acls"
        indexes = [
            IndexModel(
                [("resource_id", ASCENDING)],
                unique=True,
                name="resource_acl_resource_unique",
            )
        ]
