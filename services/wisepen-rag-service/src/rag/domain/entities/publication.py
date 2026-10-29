"""资源发布状态 Mongo 实体。"""

from dataclasses import asdict

from beanie import Document as BeanieDocument
from pymongo import ASCENDING, IndexModel

from rag.domain.models.content import ContentRevision


class ResourcePublicationEntity(BeanieDocument):
    """resource_publications 集合中的发布指针与销毁标记。"""

    resource_id: str
    staged_revision: ContentRevision | None = None
    applied_revision: ContentRevision | None = None
    destroyed: bool = False

    class Settings:
        name = "resource_publications"
        bson_encoders = {ContentRevision: asdict}
        indexes = [
            IndexModel(
                [("resource_id", ASCENDING)],
                unique=True,
                name="resource_publication_resource_unique",
            ),
            IndexModel(
                [("destroyed", ASCENDING), ("resource_id", ASCENDING)],
                name="resource_publication_destroyed_resource",
            ),
        ]
