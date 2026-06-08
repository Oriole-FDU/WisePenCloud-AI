from datetime import datetime, timezone
from typing import Dict, Optional

from beanie import Document
from beanie.odm.operators.update.general import Inc, Set
from beanie.odm.queries.update import UpdateResponse

from chat.application.rag.runtime.enums import RagIndexingStatus
from chat.application.rag.runtime.models import RagResource
from chat.application.rag.runtime.persistence.entities import (
    DocumentResourceDocument,
    NoteResourceDocument,
)
from chat.application.rag.runtime.persistence.entities.resource_documents import (
    build_resource_acl_document,
)
from chat.application.rag.runtime.persistence.interfaces import (
    DocumentResourceRepository,
    NoteResourceRepository,
)


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
            self._document_type.is_deleted == False,  # noqa: E712
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
            resource: RagResource,
            extra_fields: Dict[object, object],
    ) -> RagResource:
        now = datetime.now(timezone.utc)

        document = await self._document_type.find_one(
            self._document_type.user_id == resource.user_id,
            self._document_type.resource_id == resource.resource_id,
        ).upsert(
            Set(
                {
                    self._document_type.content: resource.content,
                    self._document_type.acl_projection: build_resource_acl_document(resource),
                    self._document_type.is_deleted: False,
                    self._document_type.indexing_status: RagIndexingStatus.PENDING,
                    self._document_type.indexing_error: None,
                    self._document_type.last_index_version: None,
                    self._document_type.updated_at: now,
                    **extra_fields,
                }
            ),
            Inc({self._document_type.version: 1}),
            on_insert=self._document_type(
                user_id=resource.user_id,
                resource_id=resource.resource_id,
                content=resource.content,
                acl_projection=build_resource_acl_document(resource),
                version=0,
                is_deleted=False,
                indexing_status=RagIndexingStatus.PENDING,
                indexing_error=None,
                last_index_version=None,
                created_at=now,
                updated_at=now,
                **extra_fields,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        return document.to_domain()

    async def mark_indexing(
            self,
            user_id: str,
            resource_id: str,
            index_version: str,
    ) -> Optional[RagResource]:
        return await self._update_index_status(
            user_id=user_id,
            resource_id=resource_id,
            status=RagIndexingStatus.INDEXING,
            index_version=index_version,
            error=None,
        )

    async def mark_index_success(
            self,
            user_id: str,
            resource_id: str,
            index_version: str,
    ) -> Optional[RagResource]:
        return await self._update_index_status(
            user_id=user_id,
            resource_id=resource_id,
            status=RagIndexingStatus.SUCCESS,
            index_version=index_version,
            error=None,
        )

    async def mark_index_failed(
            self,
            user_id: str,
            resource_id: str,
            index_version: str,
            error: str,
    ) -> Optional[RagResource]:
        return await self._update_index_status(
            user_id=user_id,
            resource_id=resource_id,
            status=RagIndexingStatus.FAILED,
            index_version=index_version,
            error=error,
        )

    async def _update_index_status(
            self,
            *,
            user_id: str,
            resource_id: str,
            status: RagIndexingStatus,
            index_version: str,
            error: Optional[str],
    ) -> Optional[RagResource]:
        document = await self._document_type.find_one(
            self._document_type.user_id == user_id,
            self._document_type.resource_id == resource_id,
            self._document_type.is_deleted == False,  # noqa: E712
        ).update(
            Set(
                {
                    self._document_type.indexing_status: status,
                    self._document_type.indexing_error: error,
                    self._document_type.last_index_version: index_version,
                    self._document_type.updated_at: datetime.now(timezone.utc),
                }
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
        return document.to_domain() if document else None

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
            resource: RagResource,
    ) -> RagResource:
        return await self._upsert_resource(
            resource=resource,
            extra_fields={
                NoteResourceDocument.title: resource.title,
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
            resource: RagResource,
    ) -> RagResource:
        return await self._upsert_resource(
            resource=resource,
            extra_fields={
                DocumentResourceDocument.document_name: resource.document_name,
            },
        )
