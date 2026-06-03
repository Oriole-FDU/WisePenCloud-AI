from dataclasses import dataclass
from typing import Optional

from chat.application.rag.enums import ResourceKind
from chat.application.rag.errors import (
    RagInvalidResourceKindError,
    RagResourceNotFoundError,
)
from chat.application.rag.models import RagResourceRef, RagResourceUpsertCommand
from chat.application.rag.service import RagService, parse_resource_kind
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException


@dataclass(frozen=True, slots=True)
class RagResourceDetailView:
    resource_id: str
    resource_kind: ResourceKind
    version: int
    content: str
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class RagResourceUpsertView:
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    material_hash: str
    pipeline_version: str
    index_version: str
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagResourceDeleteView:
    resource_id: str
    resource_kind: ResourceKind
    deleted: bool


@dataclass(frozen=True, slots=True)
class RagIndexManifestApiView:
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    material_hash: str
    pipeline_version: str
    current_index_version: str


@dataclass(frozen=True, slots=True)
class RagIndexReadinessView:
    resource_id: str
    resource_kind: ResourceKind
    target_index_version: str
    current_index_version: Optional[str]
    is_index_current: bool
    needs_indexing: bool
    can_retrieve_published_index: bool
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagIndexRebuildView:
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    target_index_version: str
    indexing_message_published: bool


class RagApiService:
    def __init__(self, *, rag_service: RagService) -> None:
        self._rag_service = rag_service

    async def upsert_note_resource(
        self,
        *,
        user_id: str,
        resource_id: str,
        content: str,
        title: Optional[str],
    ) -> RagResourceUpsertView:
        return await self._upsert_resource(
            user_id=user_id,
            resource_kind=ResourceKind.NOTE,
            resource_id=resource_id,
            content=content,
            title=title,
            document_name=None,
        )

    async def upsert_document_resource(
        self,
        *,
        user_id: str,
        resource_id: str,
        content: str,
        document_name: Optional[str],
    ) -> RagResourceUpsertView:
        return await self._upsert_resource(
            user_id=user_id,
            resource_kind=ResourceKind.DOCUMENT,
            resource_id=resource_id,
            content=content,
            title=None,
            document_name=document_name,
        )

    async def delete_note_resource(
        self,
        *,
        user_id: str,
        resource_id: str,
    ) -> RagResourceDeleteView:
        return await self._delete_resource(
            user_id=user_id,
            resource_kind=ResourceKind.NOTE,
            resource_id=resource_id,
        )

    async def delete_document_resource(
        self,
        *,
        user_id: str,
        resource_id: str,
    ) -> RagResourceDeleteView:
        return await self._delete_resource(
            user_id=user_id,
            resource_kind=ResourceKind.DOCUMENT,
            resource_id=resource_id,
        )

    async def get_note_resource_detail(
        self,
        *,
        user_id: str,
        resource_id: str,
    ) -> RagResourceDetailView:
        return await self._get_resource_detail(
            user_id=user_id,
            resource_kind=ResourceKind.NOTE,
            resource_id=resource_id,
        )

    async def get_document_resource_detail(
        self,
        *,
        user_id: str,
        resource_id: str,
    ) -> RagResourceDetailView:
        return await self._get_resource_detail(
            user_id=user_id,
            resource_kind=ResourceKind.DOCUMENT,
            resource_id=resource_id,
        )

    async def get_index_manifest(
        self,
        *,
        user_id: str,
        resource_kind: str,
        resource_id: str,
    ) -> Optional[RagIndexManifestApiView]:
        kind = _parse_api_resource_kind(resource_kind)
        try:
            manifest = await self._rag_service.get_index_manifest(
                RagResourceRef(
                    user_id=user_id,
                    resource_kind=kind,
                    resource_id=resource_id,
                )
            )
        except Exception as e:
            raise ServiceException(ChatErrorCode.RAG_STORAGE_ERROR, custom_msg=str(e))

        if manifest is None:
            return None

        return RagIndexManifestApiView(
            resource_id=manifest.resource_id,
            resource_kind=manifest.resource_kind,
            resource_version=manifest.resource_version,
            material_hash=manifest.material_hash,
            pipeline_version=manifest.pipeline_version,
            current_index_version=manifest.current_index_version,
        )

    async def get_index_readiness(
        self,
        *,
        user_id: str,
        resource_kind: str,
        resource_id: str,
    ) -> RagIndexReadinessView:
        kind = _parse_api_resource_kind(resource_kind)
        try:
            readiness = await self._rag_service.get_index_readiness(
                RagResourceRef(
                    user_id=user_id,
                    resource_kind=kind,
                    resource_id=resource_id,
                )
            )
        except RagResourceNotFoundError:
            raise ServiceException(ChatErrorCode.RAG_RESOURCE_NOT_FOUND)
        except Exception as e:
            raise ServiceException(ChatErrorCode.RAG_STORAGE_ERROR, custom_msg=str(e))

        return RagIndexReadinessView(
            resource_id=readiness.resource_id,
            resource_kind=readiness.resource_kind,
            target_index_version=readiness.target_index_version,
            current_index_version=readiness.current_index_version,
            is_index_current=readiness.is_index_current,
            needs_indexing=readiness.needs_indexing,
            can_retrieve_published_index=readiness.can_retrieve_published_index,
            indexing_message_published=readiness.indexing_message_published,
        )

    async def rebuild_index(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> RagIndexRebuildView:
        try:
            result = await self._rag_service.rebuild_index(
                RagResourceRef(
                    user_id=user_id,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                )
            )
        except RagResourceNotFoundError:
            raise ServiceException(ChatErrorCode.RAG_RESOURCE_NOT_FOUND)
        except Exception as e:
            raise ServiceException(
                ChatErrorCode.RAG_INDEXING_TASK_SUBMIT_FAILED,
                custom_msg=str(e),
            )

        return RagIndexRebuildView(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            resource_version=result.resource_version,
            target_index_version=result.target_index_version,
            indexing_message_published=result.indexing_message_published,
        )

    async def _upsert_resource(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        content: str,
        title: Optional[str],
        document_name: Optional[str],
    ) -> RagResourceUpsertView:
        try:
            result = await self._rag_service.upsert_resource(
                RagResourceUpsertCommand(
                    user_id=user_id,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    content=content,
                    title=title,
                    document_name=document_name,
                )
            )
        except Exception as e:
            raise ServiceException(ChatErrorCode.RAG_STORAGE_ERROR, custom_msg=str(e))

        return RagResourceUpsertView(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            resource_version=result.resource_version,
            material_hash=result.material_hash,
            pipeline_version=result.pipeline_version,
            index_version=result.index_version,
            indexing_message_published=result.indexing_message_published,
        )

    async def _delete_resource(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> RagResourceDeleteView:
        try:
            result = await self._rag_service.delete_resource(
                RagResourceRef(
                    resource_kind=resource_kind,
                    user_id=user_id,
                    resource_id=resource_id,
                )
            )
        except Exception as e:
            raise ServiceException(ChatErrorCode.RAG_STORAGE_ERROR, custom_msg=str(e))

        return RagResourceDeleteView(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            deleted=result.deleted,
        )

    async def _get_resource_detail(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> RagResourceDetailView:
        try:
            resource = await self._rag_service.get_resource(
                RagResourceRef(
                    resource_kind=resource_kind,
                    user_id=user_id,
                    resource_id=resource_id,
                )
            )
        except RagResourceNotFoundError:
            raise ServiceException(ChatErrorCode.RAG_RESOURCE_NOT_FOUND)
        except Exception as e:
            raise ServiceException(ChatErrorCode.RAG_STORAGE_ERROR, custom_msg=str(e))

        return RagResourceDetailView(
            resource_id=resource.resource_id,
            resource_kind=resource.resource_kind,
            version=resource.version,
            content=resource.content,
            is_deleted=resource.is_deleted,
        )


def _parse_api_resource_kind(raw: str) -> ResourceKind:
    try:
        return parse_resource_kind(raw)
    except RagInvalidResourceKindError:
        raise ServiceException(
            ChatErrorCode.CUSTOM_PROVIDER_INVALID_MODE,
            custom_msg="Unsupported resource_kind",
        )
