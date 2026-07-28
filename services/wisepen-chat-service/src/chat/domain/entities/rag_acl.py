from __future__ import annotations

from typing import ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


class RagComputedGroupAclDocument(BaseModel):
    group_id: str
    is_readable: bool
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)


class RagAclProjectionDocument(Document):
    """Kafka 驱动的 Resource VIEW 权限本地投影。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = Field(default_factory=list)
    excluded_read_users: list[str] = Field(default_factory=list)
    computed_group_acls: list[RagComputedGroupAclDocument] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_acl_projections"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING)],
                name="idx_rag_acl_projection_resource_id",
                unique=True,
            )
        ]
