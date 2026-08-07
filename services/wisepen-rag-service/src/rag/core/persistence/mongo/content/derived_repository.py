from __future__ import annotations

from collections.abc import Mapping, Sequence

from beanie.operators import In
from pymongo import UpdateOne

from rag.domain.repositories import (
    KnowledgeGraphExtractionRepository,
    RagContextIndexingRepository,
)
from rag.domain.entities.rag_content import (
    RagContextIndexingDocument,
    RagGraphExtractionDocument,
)


class MongoRagContextIndexingRepository(RagContextIndexingRepository):
    """按资源持久化 Contextual Indexing 派生文本。"""

    async def get_many(
        self,
        *,
        resource_id: str,
        keys: Sequence[str],
    ) -> Mapping[str, str]:
        unique_keys = tuple(dict.fromkeys(keys))
        if not unique_keys:
            return {}
        documents = await RagContextIndexingDocument.find(
            RagContextIndexingDocument.resource_id == resource_id,
            In(RagContextIndexingDocument.context_key, unique_keys),
        ).to_list()
        return {
            document.context_key: document.indexing_context
            for document in documents
        }

    async def set_many(
        self,
        *,
        resource_id: str,
        values: Mapping[str, str],
    ) -> None:
        if not values:
            return
        await RagContextIndexingDocument.get_pymongo_collection().bulk_write(
            [
                UpdateOne(
                    {
                        "resource_id": resource_id,
                        "context_key": context_key,
                    },
                    {
                        "$set": {
                            "indexing_context": indexing_context,
                        },
                        "$setOnInsert": {
                            "resource_id": resource_id,
                            "context_key": context_key,
                        },
                    },
                    upsert=True,
                )
                for context_key, indexing_context in values.items()
            ]
        )

    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None:
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return
        await RagContextIndexingDocument.find(
            In(RagContextIndexingDocument.resource_id, unique_resource_ids)
        ).delete()


class MongoKnowledgeGraphExtractionRepository(KnowledgeGraphExtractionRepository):
    """按资源持久化 GraphRAG SDK 候选图派生结果。"""

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, str]:
        unique_keys = tuple(dict.fromkeys(keys))
        if not unique_keys:
            return {}
        documents = await RagGraphExtractionDocument.find(
            In(RagGraphExtractionDocument.extraction_key, unique_keys)
        ).to_list()
        return {
            document.extraction_key: document.graph_payload
            for document in documents
        }

    async def set_many(
        self,
        *,
        resource_id: str,
        values: Mapping[str, str],
    ) -> None:
        if not values:
            return
        await RagGraphExtractionDocument.get_pymongo_collection().bulk_write(
            [
                UpdateOne(
                    {
                        "resource_id": resource_id,
                        "extraction_key": extraction_key,
                    },
                    {
                        "$set": {
                            "graph_payload": graph_payload,
                        },
                        "$setOnInsert": {
                            "resource_id": resource_id,
                            "extraction_key": extraction_key,
                        },
                    },
                    upsert=True,
                )
                for extraction_key, graph_payload in values.items()
            ]
        )
