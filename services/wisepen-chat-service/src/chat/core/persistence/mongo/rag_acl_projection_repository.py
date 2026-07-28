from __future__ import annotations

from collections.abc import Sequence

from bson import ObjectId
from beanie.operators import In
from pymongo.errors import DuplicateKeyError
from chat.application.rag.acl import (
    RagAclProjector,
    RagComputedGroupAclProjection,
    RagResourceAclProjection,
)
from chat.domain.entities.rag_acl import (
    RagAclProjectionDocument,
    RagComputedGroupAclDocument,
)

_RESOURCE_COLLECTION = "wisepen_resource_items"


class MongoRagAclProjectionRepository:
    """读取 Java Resource 权威集合并保存 RAG ACL 派生投影。"""

    __slots__ = ("_projector", "_resource_database_name")

    def __init__(
        self,
        *,
        projector: RagAclProjector,
        resource_database_name: str,
    ) -> None:
        self._projector = projector
        self._resource_database_name = resource_database_name

    async def get_projection(self, resource_id: str) -> RagResourceAclProjection | None:
        document = await RagAclProjectionDocument.find_one(
            RagAclProjectionDocument.resource_id == resource_id
        )
        if document is None:
            return None
        return _to_projection(document)

    async def get_projections(
        self,
        resource_ids: Sequence[str],
    ) -> dict[str, RagResourceAclProjection]:
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}
        documents = await RagAclProjectionDocument.find(
            In(RagAclProjectionDocument.resource_id, unique_resource_ids)
        ).to_list()
        return {
            document.resource_id: _to_projection(document) for document in documents
        }

    async def load_authoritative_projection(
        self,
        resource_id: str,
    ) -> RagResourceAclProjection | None:
        projection_collection = RagAclProjectionDocument.get_pymongo_collection()
        resource_collection = projection_collection.database.client[
            self._resource_database_name
        ][_RESOURCE_COLLECTION]
        raw = await resource_collection.find_one({"_id": ObjectId(resource_id)})
        if raw is None:
            return None
        return self._projector.from_resource_item({**raw, "_id": str(raw["_id"])})

    async def upsert_projection(self, projection: RagResourceAclProjection) -> None:
        collection = RagAclProjectionDocument.get_pymongo_collection()
        try:
            await collection.update_one(
                {
                    "resource_id": projection.resource_id,
                    "acl_revision": {"$lte": projection.acl_revision},
                },
                {
                    "$set": {
                        "acl_revision": projection.acl_revision,
                        "owner_id": projection.owner_id,
                        "readable_users": list(projection.readable_users),
                        "excluded_read_users": list(
                            projection.excluded_read_users
                        ),
                        "computed_group_acls": [
                            item.model_dump()
                            for item in _group_documents(projection)
                        ],
                    },
                    "$setOnInsert": {"resource_id": projection.resource_id},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # 唯一 resource_id 已存在且 revision 更新，当前旧投影不覆盖。
            return

    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None:
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return
        await RagAclProjectionDocument.find(
            In(RagAclProjectionDocument.resource_id, unique_resource_ids)
        ).delete()


def _to_projection(document: RagAclProjectionDocument) -> RagResourceAclProjection:
    return RagResourceAclProjection(
        resource_id=document.resource_id,
        acl_revision=document.acl_revision,
        owner_id=document.owner_id,
        readable_users=tuple(document.readable_users),
        excluded_read_users=tuple(document.excluded_read_users),
        computed_group_acls=tuple(
            RagComputedGroupAclProjection(
                group_id=item.group_id,
                is_readable=item.is_readable,
                readable_users=tuple(item.readable_users),
                excluded_read_users=tuple(item.excluded_read_users),
            )
            for item in document.computed_group_acls
        ),
    )


def _group_documents(
    projection: RagResourceAclProjection,
) -> list[RagComputedGroupAclDocument]:
    return [
        RagComputedGroupAclDocument(
            group_id=item.group_id,
            is_readable=item.is_readable,
            readable_users=list(item.readable_users),
            excluded_read_users=list(item.excluded_read_users),
        )
        for item in projection.computed_group_acls
    ]
