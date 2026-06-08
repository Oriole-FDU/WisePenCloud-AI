from datetime import datetime, timezone
from typing import Dict, List, Optional

from beanie.odm.operators.update.general import Set

from chat.application.rag.enums import ResourceKind
from chat.application.rag.permissions import (
    RagGroupRole,
    can_view,
)
from chat.application.rag.runtime.models import RagIndexManifest
from chat.application.rag.runtime.persistence.entities import \
    RagIndexManifestDocument
from chat.application.rag.runtime.persistence.entities.manifest_documents import (
    build_manifest_acl_index_fields,
)
from chat.application.rag.runtime.persistence.interfaces import RagManifestRepository


class MongoManifestRepository(RagManifestRepository):

    async def get_by_resource(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
    ) -> Optional[RagIndexManifest]:
        document = await RagIndexManifestDocument.find_one(
            {
            "user_id": user_id,
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            }
        )

        return document.to_domain() if document else None

    async def publish(self, manifest: RagIndexManifest) -> RagIndexManifest:
        now = datetime.now(timezone.utc)
        acl_index_fields = build_manifest_acl_index_fields(manifest)

        await RagIndexManifestDocument.find_one(
            RagIndexManifestDocument.user_id == manifest.user_id,
            RagIndexManifestDocument.resource_kind == manifest.resource_kind,
            RagIndexManifestDocument.resource_id == manifest.resource_id,
        ).upsert(
            Set(
                {
                    "resource_version": manifest.resource_version,
                    "material_hash": manifest.material_hash,
                    "pipeline_version": manifest.pipeline_version,
                    "current_index_version": manifest.current_index_version,
                    "updated_at": now,
                    **acl_index_fields,
                }
            ),
            on_insert=RagIndexManifestDocument(
                user_id=manifest.user_id,
                resource_kind=manifest.resource_kind,
                resource_id=manifest.resource_id,
                resource_version=manifest.resource_version,
                material_hash=manifest.material_hash,
                pipeline_version=manifest.pipeline_version,
                current_index_version=manifest.current_index_version,
                created_at=now,
                updated_at=now,
                **acl_index_fields,
            ),
        )

        return manifest

    async def delete(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
    ) -> None:
        await RagIndexManifestDocument.find_one(
            {
                "user_id": user_id,
                "resource_kind": resource_kind,
                "resource_id": resource_id,
            }
        ).delete_one()

    async def list_by_user(
            self,
            user_id: str,
    ) -> List[RagIndexManifest]:
        documents = await RagIndexManifestDocument.find(
            {
                "user_id": user_id,
            }).to_list()

        return [document.to_domain() for document in documents]

    async def list_visible_manifests(
            self,
            user_id: str,
            group_role_map: Dict[str, RagGroupRole],
            resource_kinds: Optional[List[ResourceKind]] = None,
    ) -> List[RagIndexManifest]:
        query = _build_visible_manifest_query(
            user_id=user_id,
            group_role_map=group_role_map,
            resource_kinds=resource_kinds,
        )
        documents = await RagIndexManifestDocument.find(query).to_list()
        manifests = [document.to_domain() for document in documents]

        return [
            manifest
            for manifest in manifests
            if can_view(
                user_id=user_id,
                group_role_map=group_role_map,
                projection=manifest.acl_projection,
            )
        ]

    async def list_active_manifests(
            self,
            limit: int = 100,
    ) -> List[RagIndexManifest]:
        documents = await (
            RagIndexManifestDocument.find()
            .sort("-updated_at")
            .limit(limit)
            .to_list()
        )

        return [document.to_domain() for document in documents]


def _build_visible_manifest_query(
        *,
        user_id: str,
        group_role_map: Dict[str, RagGroupRole],
        resource_kinds: Optional[List[ResourceKind]],
) -> Dict:
    managed_group_ids = [
        group_id
        for group_id, role in group_role_map.items()
        if role in (RagGroupRole.ADMIN, RagGroupRole.OWNER)
    ]
    joined_group_ids = [
        group_id
        for group_id, role in group_role_map.items()
        if role != RagGroupRole.NOT_MEMBER
    ]

    should = [
        {"owner_id": user_id},
        {"specified_view_user_ids": user_id},
        {"member_view_user_ids": user_id},
    ]
    if managed_group_ids:
        should.append({"admin_group_ids": {"$in": managed_group_ids}})
    if joined_group_ids:
        should.append({"member_view_group_ids": {"$in": joined_group_ids}})

    query: Dict = {
        "is_deleted": False,
        "is_trashed": False,
        "is_physically_destroyed": False,
        "$or": should,
    }
    if resource_kinds is not None:
        query["resource_kind"] = {"$in": list(resource_kinds)}
    return query
