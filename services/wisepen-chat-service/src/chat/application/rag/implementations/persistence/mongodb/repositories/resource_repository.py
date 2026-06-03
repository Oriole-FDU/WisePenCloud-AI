from datetime import datetime, timezone
from typing import Dict, Optional

from beanie import Document
from beanie.odm.operators.update.general import Inc, Set
from beanie.odm.queries.update import UpdateResponse
from chat.application.rag.implementations.persistence.mongodb.entities.resource_documents import (
    DocumentResourceDocument,
    NoteResourceDocument,
)

from chat.application.rag.domain.ports import (
    DocumentResourceRepository,
    NoteResourceRepository,
)
from chat.application.rag.domain.resource_lifecycle import RagResource


class BaseMongoResourceRepository:

    def __init__(
            self,
            document_type: type[Document],
    ) -> None:
        self._document_type = document_type

    async def get_by_id(
            self,
            user_id: str,
            resource_id: str,
    ) -> Optional[RagResource]:
        document = await self._find_document(
            user_id=user_id,
            resource_id=resource_id,
        )

        return document.to_domain() if document else None

    async def mark_deleted(
            self,
            user_id: str,
            resource_id: str,
    ) -> Optional[RagResource]:
        document = await self._document_type.find_one(
            self._document_type.user_id == user_id,
            self._document_type.resource_id == resource_id,
            self._document_type.is_deleted == False,
        ).update(
            Set(
                {
                    self._document_type.is_deleted: True,
                    self._document_type.updated_at: datetime.now(timezone.utc),
                }
            ),
            Inc({self._document_type.version: 1}),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
        return document.to_domain() if document else None

    async def _upsert_resource(
            self,
            *,
            user_id: str,
            resource_id: str,
            content: str,
            extra_fields: Dict[object, object],
    ) -> RagResource:
        now = datetime.now(timezone.utc)

        document = await self._document_type.find_one(
            self._document_type.user_id == user_id,
            self._document_type.resource_id == resource_id,
        ).upsert(
            Set(
                {
                    self._document_type.content: content,
                    self._document_type.is_deleted: False,
                    self._document_type.updated_at: now,
                    **extra_fields,
                }
            ),
            Inc({self._document_type.version: 1}),
            on_insert=self._document_type(
                user_id=user_id,
                resource_id=resource_id,
                content=content,
                version=0,
                is_deleted=False,
                created_at=now,
                updated_at=now,
                **extra_fields,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        return document.to_domain()

    async def _find_document(
            self,
            *,
            user_id: str,
            resource_id: str,
    ):
        return await self._document_type.find_one(
            self._document_type.user_id == user_id,
            self._document_type.resource_id == resource_id,
        )


class MongoNoteResourceRepository(
    BaseMongoResourceRepository,
    NoteResourceRepository,
):

    def __init__(self) -> None:
        super().__init__(NoteResourceDocument)

    async def upsert(
            self,
            user_id: str,
            resource_id: str,
            content: str,
            title: Optional[str] = None,
    ) -> RagResource:
        return await self._upsert_resource(
            user_id=user_id,
            resource_id=resource_id,
            content=content,
            extra_fields={
                NoteResourceDocument.title: title,
            },
        )


class MongoDocumentResourceRepository(
    BaseMongoResourceRepository,
    DocumentResourceRepository,
):

    def __init__(self) -> None:
        super().__init__(DocumentResourceDocument)

    async def upsert(
            self,
            user_id: str,
            resource_id: str,
            content: str,
            document_name: Optional[str] = None,
    ) -> RagResource:
        return await self._upsert_resource(
            user_id=user_id,
            resource_id=resource_id,
            content=content,
            extra_fields={
                DocumentResourceDocument.document_name: document_name,
            },
        )