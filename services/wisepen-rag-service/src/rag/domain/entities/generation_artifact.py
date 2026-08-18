"""模型生成派生产物的 Beanie Mongo 实体。"""

from typing import ClassVar, Literal

from beanie import Document
from pymongo import ASCENDING, IndexModel


class GenerationArtifactEntity(Document):
    """按资源和生成类别保存可复用的模型输出。"""

    resource_id: str
    artifact_kind: Literal["context", "graph"]
    artifact_key: str
    payload: str

    class Settings:
        name = "wisepen_rag_v2_generation_artifact_store"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [
                    ("resource_id", ASCENDING),
                    ("artifact_kind", ASCENDING),
                    ("artifact_key", ASCENDING),
                ],
                name="idx_rag_v2_generation_artifact_resource_kind_key",
                unique=True,
            ),
        ]
