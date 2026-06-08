from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from chat.api.schemas.search_provider import (
    SearchProviderConfigResponse,
    SetCustomSearchProviderRequest,
    SetSearchProviderModeRequest,
)
from chat.application.api_service.search_provider import (
    SearchProviderConfigApiService,
    SearchProviderConfigView,
)
from chat.container import Container
from common.core.domain import R
from common.security import require_login

router = APIRouter()


@router.get("/getConfig", response_model=R[SearchProviderConfigResponse])
@inject
async def get_search_provider_config(
    user_id: str = Depends(require_login),
    service: SearchProviderConfigApiService = Depends(
        Provide[Container.search_provider_config_api_service]
    ),
):
    return R.success(data=_to_response(await service.get_config(user_id=user_id)))


@router.post("/setMode", response_model=R[SearchProviderConfigResponse])
@inject
async def set_search_provider_mode(
    req: SetSearchProviderModeRequest,
    user_id: str = Depends(require_login),
    service: SearchProviderConfigApiService = Depends(
        Provide[Container.search_provider_config_api_service]
    ),
):
    config = await service.set_mode(user_id=user_id, mode=req.mode)
    return R.success(data=_to_response(config))


@router.post("/setCustomProvider", response_model=R[SearchProviderConfigResponse])
@inject
async def set_custom_search_provider(
    req: SetCustomSearchProviderRequest,
    user_id: str = Depends(require_login),
    service: SearchProviderConfigApiService = Depends(
        Provide[Container.search_provider_config_api_service]
    ),
):
    config = await service.set_custom_provider(
        user_id=user_id,
        provider=req.provider,
        api_key=req.api_key,
    )
    return R.success(data=_to_response(config))


@router.post("/clearCustomProvider", response_model=R[SearchProviderConfigResponse])
@inject
async def clear_custom_search_provider(
    user_id: str = Depends(require_login),
    service: SearchProviderConfigApiService = Depends(
        Provide[Container.search_provider_config_api_service]
    ),
):
    config = await service.clear_custom_provider(user_id=user_id)
    return R.success(data=_to_response(config))


@router.post("/verifyProvider", response_model=R[SearchProviderConfigResponse])
@inject
async def verify_custom_search_provider(
    user_id: str = Depends(require_login),
    service: SearchProviderConfigApiService = Depends(
        Provide[Container.search_provider_config_api_service]
    ),
):
    config = await service.verify(user_id=user_id)
    return R.success(data=_to_response(config))


def _to_response(config: SearchProviderConfigView) -> SearchProviderConfigResponse:
    return SearchProviderConfigResponse(
        provider_mode=config.provider_mode,
        provider=config.provider,
        masked_key=config.masked_key,
        is_valid=config.is_valid,
    )
