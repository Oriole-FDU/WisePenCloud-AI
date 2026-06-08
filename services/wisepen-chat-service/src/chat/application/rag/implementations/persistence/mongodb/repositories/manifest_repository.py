from datetime import datetime, timezone
from typing import List, Optional

from beanie.odm.operators.update.general import Set
from chat.application.rag.implementations.persistence.mongodb.entities.manifest_documents import \
    RagIndexManifestDocument

from chat.application.rag.domain.index_publication import RagIndexManifest
from chat.application.rag.domain.ports import RagManifestRepository
from chat.application.rag.enums import ResourceKind


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