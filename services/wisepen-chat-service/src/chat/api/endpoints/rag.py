from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from chat.api.schemas.rag import (
    RagIndexManifestResponse,
    RagIndexReadinessResponse,
    RagIndexRebuildResponse,
    RagIndexResourceRequest,
    RagResourceDeleteRequest,
    RagResourceDeleteResponse,
    RagResourceDetailResponse,
    RagResourceUpsertRequest,
    RagResourceUpsertResponse,
)
from chat.application.api_service.rag import RagApiService
from chat.application.rag.enums import ResourceKind
from chat.container import Container
from common.core.domain import R
from common.security import require_login

router = APIRouter()


@router.post("/upsertNote", response_model=R[RagResourceUpsertResponse])
@inject
async def upsert_note_resource(
    request: RagResourceUpsertRequest,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """创建或更新当前用户的 note RAG 资源。"""
    result = await service.upsert_note_resource(
        user_id=user_id,
        resource_id=request.resource_id,
        content=request.content,
        title=request.title,
    )

    return R.success(
        data=RagResourceUpsertResponse(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            resource_version=result.resource_version,
            material_hash=result.material_hash,
            pipeline_version=result.pipeline_version,
            index_version=result.index_version,
            indexing_message_published=result.indexing_message_published,
        )
    )


@router.post("/upsertDocument", response_model=R[RagResourceUpsertResponse])
@inject
async def upsert_document_resource(
    request: RagResourceUpsertRequest,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """创建或更新当前用户的 document RAG 资源。"""
    result = await service.upsert_document_resource(
        user_id=user_id,
        resource_id=request.resource_id,
        content=request.content,
        document_name=request.document_name,
    )

    return R.success(
        data=RagResourceUpsertResponse(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            resource_version=result.resource_version,
            material_hash=result.material_hash,
            pipeline_version=result.pipeline_version,
            index_version=result.index_version,
            indexing_message_published=result.indexing_message_published,
        )
    )


@router.post("/deleteNote", response_model=R[RagResourceDeleteResponse])
@inject
async def delete_note_resource(
    request: RagResourceDeleteRequest,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """删除当前用户的 note RAG 资源。"""
    result = await service.delete_note_resource(
        user_id=user_id,
        resource_id=request.resource_id,
    )

    return R.success(
        data=RagResourceDeleteResponse(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            deleted=result.deleted,
        )
    )


@router.post("/deleteDocument", response_model=R[RagResourceDeleteResponse])
@inject
async def delete_document_resource(
    request: RagResourceDeleteRequest,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """删除当前用户的 document RAG 资源。"""
    result = await service.delete_document_resource(
        user_id=user_id,
        resource_id=request.resource_id,
    )

    return R.success(
        data=RagResourceDeleteResponse(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            deleted=result.deleted,
        )
    )


@router.get("/getNoteDetail", response_model=R[RagResourceDetailResponse])
@inject
async def get_note_resource_detail(
    resource_id: str,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """获取当前用户的 note RAG 资源详情。"""
    resource = await service.get_note_resource_detail(
        user_id=user_id,
        resource_id=resource_id,
    )

    return R.success(
        data=RagResourceDetailResponse(
            resource_id=resource.resource_id,
            resource_kind=resource.resource_kind,
            version=resource.version,
            content=resource.content,
            is_deleted=resource.is_deleted,
            indexing_status=resource.indexing_status,
            indexing_error=resource.indexing_error,
            last_index_version=resource.last_index_version,
        )
    )


@router.get("/getDocumentDetail", response_model=R[RagResourceDetailResponse])
@inject
async def get_document_resource_detail(
    resource_id: str,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """获取当前用户的 document RAG 资源详情。"""
    resource = await service.get_document_resource_detail(
        user_id=user_id,
        resource_id=resource_id,
    )

    return R.success(
        data=RagResourceDetailResponse(
            resource_id=resource.resource_id,
            resource_kind=resource.resource_kind,
            version=resource.version,
            content=resource.content,
            is_deleted=resource.is_deleted,
            indexing_status=resource.indexing_status,
            indexing_error=resource.indexing_error,
            last_index_version=resource.last_index_version,
        )
    )


@router.get("/getIndexManifest", response_model=R[RagIndexManifestResponse])
@inject
async def get_index_manifest(
    resource_kind: ResourceKind,
    resource_id: str,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """获取指定资源当前发布的 RAG Manifest。"""
    manifest = await service.get_index_manifest(
        user_id=user_id,
        resource_kind=resource_kind,
        resource_id=resource_id,
    )
    if manifest is None:
        return R.success(data=None)

    return R.success(
        data=RagIndexManifestResponse(
            resource_id=manifest.resource_id,
            resource_kind=manifest.resource_kind,
            resource_version=manifest.resource_version,
            material_hash=manifest.material_hash,
            pipeline_version=manifest.pipeline_version,
            current_index_version=manifest.current_index_version,
        )
    )


@router.get("/getIndexReadiness", response_model=R[RagIndexReadinessResponse])
@inject
async def get_index_readiness(
    resource_kind: ResourceKind,
    resource_id: str,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """获取指定资源索引就绪状态。"""
    readiness = await service.get_index_readiness(
        user_id=user_id,
        resource_kind=resource_kind,
        resource_id=resource_id,
    )

    return R.success(
        data=RagIndexReadinessResponse(
            resource_id=readiness.resource_id,
            resource_kind=readiness.resource_kind,
            target_index_version=readiness.target_index_version,
            current_index_version=readiness.current_index_version,
            indexing_status=readiness.indexing_status,
            indexing_error=readiness.indexing_error,
            last_index_version=readiness.last_index_version,
            is_index_current=readiness.is_index_current,
            needs_indexing=readiness.needs_indexing,
            can_retrieve_published_index=readiness.can_retrieve_published_index,
            indexing_message_published=readiness.indexing_message_published,
        )
    )


@router.post("/rebuildIndex", response_model=R[RagIndexRebuildResponse], status_code=200)
@inject
async def rebuild_index(
    request: RagIndexResourceRequest,
    user_id: str = Depends(require_login),
    service: RagApiService = Depends(Provide[Container.rag_api_service]),
):
    """重新投递指定资源的 RAG 索引任务。"""
    result = await service.rebuild_index(
        user_id=user_id,
        resource_kind=request.resource_kind,
        resource_id=request.resource_id,
    )

    return R.success(
        data=RagIndexRebuildResponse(
            resource_id=result.resource_id,
            resource_kind=result.resource_kind,
            resource_version=result.resource_version,
            target_index_version=result.target_index_version,
            indexing_message_published=result.indexing_message_published,
        )
    )
