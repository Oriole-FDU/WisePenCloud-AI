"""Beanie adapter：按资源维护模型生成派生产物。"""

from collections.abc import Mapping, Sequence
from typing import Literal

from beanie.operators import In
from pymongo import UpdateOne

from rag.domain.entities import GenerationArtifactEntity
from rag.domain.repositories.mongo.generation_artifact_store import (
    GenerationArtifactStore,
)


class MongoGenerationArtifactStore(GenerationArtifactStore):
    """隔离资源和派生产物类别，提供批量命中、覆盖及资源删除。"""

    async def get_many(
        self,
        *,
        resource_id: str,
        artifact_kind: Literal["context", "graph"],
        artifact_keys: Sequence[str],
    ) -> Mapping[str, str]:
        unique_artifact_keys = list(dict.fromkeys(artifact_keys))
        if not unique_artifact_keys:
            return {}

        records = await GenerationArtifactEntity.find(
            GenerationArtifactEntity.resource_id == resource_id,
            GenerationArtifactEntity.artifact_kind == artifact_kind,
            In(GenerationArtifactEntity.artifact_key, unique_artifact_keys),
        ).to_list()
        return {record.artifact_key: record.payload for record in records}

    async def set_many(
        self,
        *,
        resource_id: str,
        artifact_kind: Literal["context", "graph"],
        artifacts: Mapping[str, str],
    ) -> None:
        if not artifacts:
            return

        await GenerationArtifactEntity.get_pymongo_collection().bulk_write(
            [
                UpdateOne(
                    {
                        "resource_id": resource_id,
                        "artifact_kind": artifact_kind,
                        "artifact_key": artifact_key,
                    },
                    {
                        "$set": _to_document(
                            resource_id=resource_id,
                            artifact_kind=artifact_kind,
                            artifact_key=artifact_key,
                            payload=payload,
                        )
                    },
                    upsert=True,
                )
                for artifact_key, payload in artifacts.items()
            ]
        )

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return

        await GenerationArtifactEntity.find(
            In(GenerationArtifactEntity.resource_id, unique_resource_ids)
        ).delete()


def _to_document(
    *,
    resource_id: str,
    artifact_kind: Literal["context", "graph"],
    artifact_key: str,
    payload: str,
) -> dict[str, object]:
    return {
        "resource_id": resource_id,
        "artifact_kind": artifact_kind,
        "artifact_key": artifact_key,
        "payload": payload,
    }
