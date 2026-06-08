from datetime import datetime, timezone

from beanie import Document
from pydantic import ConfigDict, Field
from pymongo import ASCENDING, IndexModel

from chat.application.rag.domain.index_publication import RagIndexManifest
from chat.application.rag.enums import ResourceKind


class RagIndexManifestDocument(Document):

    user_id: str = Field(..., description="用户 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_id: str = Field(..., description="资源 ID")

    resource_version: int = Field(..., description="当前发布索引对应的资源事实版本")
    material_hash: str = Field(..., description="当前发布索引对应的资源材料 hash")
    pipeline_version: str = Field(..., description="当前发布索引对应的 RAG pipeline 版本")
    current_index_version: str = Field(..., description="当前线上检索使用的 index_version")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(frozen=False)

    class Settings:
        name = "rag_index_manifests"
        indexes = [
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("resource_id", ASCENDING),
                ],
                unique=True,
                name="uq_manifest_resource",
            ),
        ]

    def to_domain(self) -> RagIndexManifest:
        return RagIndexManifest(
            user_id=self.user_id,
            resource_kind=self.resource_kind,
            resource_id=self.resource_id,
            resource_version=self.resource_version,
            material_hash=self.material_hash,
            pipeline_version=self.pipeline_version,
            current_index_version=self.current_index_version,
        )