from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from chat.api.schemas.web_search import (
    CreateWebSearchCredentialRequest,
    SetActiveWebSearchCredentialRequest,
    WebSearchCredentialResponse,
)
from chat.container import Container
from chat.core.persistence.mongo.web_search_credential_repository import (
    MongoWebSearchCredentialRepository,
)
from chat.domain.entities.web_search_credential import (
    WebSearchCredential,
    WebSearchCredentialSource,
)
from chat.domain.error_codes import ChatErrorCode
from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import require_login

router = APIRouter()


def to_response(credential: WebSearchCredential) -> WebSearchCredentialResponse:
    return WebSearchCredentialResponse(
        user_id=credential.user_id,
        provider=credential.provider,
        source=credential.source,
        api_key_fingerprint=credential.api_key_fingerprint,
        is_active=credential.is_active,
        created_at=credential.created_at.isoformat(),
        updated_at=credential.updated_at.isoformat(),
    )


@router.get("/listWebSearchCredentials", response_model=R[list[WebSearchCredentialResponse]])
@inject
async def list_web_search_credentials(
        user_id: str = Depends(require_login),
        credential_repo: MongoWebSearchCredentialRepository = Depends(Provide[Container.web_search_credential_repo]),
):
    credentials = await credential_repo.list_user_credentials(user_id=user_id)
    existing_sources = {item.source for item in credentials}
    platform_sources = (
        WebSearchCredentialSource.PLATFORM_DEFAULT,
        WebSearchCredentialSource.PLATFORM_MEMBER,
    )
    missing_platform_credentials = [
        await credential_repo.init_platform_credential(user_id=user_id, source=source)
        for source in platform_sources
        if source not in existing_sources
    ]
    credentials = [*missing_platform_credentials, *credentials]
    return R.success(data=[to_response(item) for item in credentials])


@router.post("/createWebSearchCredential", response_model=R[WebSearchCredentialResponse], status_code=200)
@inject
async def create_web_search_credential(
        req: CreateWebSearchCredentialRequest,
        user_id: str = Depends(require_login),
        credential_repo: MongoWebSearchCredentialRepository = Depends(Provide[Container.web_search_credential_repo]),
):
    if req.source != WebSearchCredentialSource.CUSTOM:
        raise ServiceException(ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, custom_msg="只允许上传 custom 搜索凭证")

    credential = await credential_repo.upsert_custom_credential(
        user_id=user_id,
        provider=req.provider,
        api_key=req.api_key,
    )
    return R.success(data=to_response(credential))


@router.post("/setActiveWebSearchCredential", response_model=R[WebSearchCredentialResponse], status_code=200)
@inject
async def set_active_web_search_credential(
        req: SetActiveWebSearchCredentialRequest,
        user_id: str = Depends(require_login),
        credential_repo: MongoWebSearchCredentialRepository = Depends(Provide[Container.web_search_credential_repo]),
):
    credential = await credential_repo.set_active_credential(
        user_id=user_id,
        source=req.source,
        provider=req.provider,
    )
    return R.success(data=to_response(credential))
