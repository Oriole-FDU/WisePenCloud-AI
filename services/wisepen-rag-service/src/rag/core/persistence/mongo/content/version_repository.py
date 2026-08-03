from __future__ import annotations

from collections.abc import Sequence

from beanie.operators import In
from rag.application.rag.ingestion import RagProjectionCheckpoint
from rag.domain.entities.rag_content import RagProjectionCheckpointDocument


class MongoRagContentCheckpointRepository:
    """版本侧仓储：读取正文投影 checkpoint 和当前 applied revision。"""

    async def get_checkpoint(
        self,
        resource_id: str,
    ) -> RagProjectionCheckpoint | None:
        return await load_content_checkpoint(resource_id)

    async def get_applied_revisions(
        self,
        resource_ids: Sequence[str],
    ) -> dict[str, str]:
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}
        documents = await RagProjectionCheckpointDocument.find(
            In(RagProjectionCheckpointDocument.resource_id, unique_resource_ids)
        ).to_list()
        return {
            document.resource_id: document.applied_content_revision
            for document in documents
            if document.applied_content_revision is not None
        }


async def load_content_checkpoint(
    resource_id: str,
) -> RagProjectionCheckpoint | None:
    document = await RagProjectionCheckpointDocument.find_one(
        RagProjectionCheckpointDocument.resource_id == resource_id
    )
    if document is None:
        return None
    return RagProjectionCheckpoint(
        resource_id=document.resource_id,
        staged_content_revision=document.staged_content_revision,
        staged_document_version=document.staged_document_version,
        applied_content_revision=document.applied_content_revision,
        applied_document_version=document.applied_document_version,
    )


async def load_applied_content_revision(resource_id: str) -> str | None:
    checkpoint = await load_content_checkpoint(resource_id)
    if checkpoint is None:
        return None
    return checkpoint.applied_content_revision
