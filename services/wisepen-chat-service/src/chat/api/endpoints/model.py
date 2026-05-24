from typing import Dict

from beanie import PydanticObjectId
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from chat.api.schemas.model import (
    AvailableModelsResponse,
    BindModelProviderRequest,
    CreateUserModelRequest,
    CreateUserProviderRequest,
    DeleteUserModelRequest,
    DeleteUserProviderRequest,
    ListUserModelsResponse,
    ListUserProvidersResponse,
    ModelProviderMappingResponse,
    ModelResponse,
    ProviderResponse,
    UnbindModelProviderRequest,
    UpdateUserModelRequest,
    UpdateUserProviderRequest,
)
from chat.container import Container
from chat.domain.entities.model import Model, ModelProviderMapping
from chat.domain.entities.provider import Provider
from chat.domain.repositories import ModelRepository, ProviderRepository
from chat.domain.repositories.model_repo import ModelInfo
from common.core.domain import R
from common.security import require_login

router = APIRouter()


def to_provider_response(provider: Provider) -> ProviderResponse:
    return ProviderResponse(
        id=str(provider.id) if provider.id else "",
        name=provider.name,
        api_base_url=provider.api_base_url,
        api_key_fingerprint=provider.api_key_fingerprint,
        scope=provider.scope,
        type=provider.type,
        is_active=provider.is_active,
        usage_tokens=provider.usage_tokens,
        billable_usage_tokens=provider.billable_usage_tokens,
    )


def to_mapping_response(
    mapping: ModelProviderMapping,
    providers: Dict[str, Provider] = None,
) -> ModelProviderMappingResponse:
    providers = providers or {}
    provider = providers.get(str(mapping.provider_id), None)
    return ModelProviderMappingResponse(
        model_id=str(mapping.model_id),
        provider_id=str(mapping.provider_id),
        provider_name=provider.name if provider is not None else None,
        provider_model_name=mapping.provider_model_name,
        is_preferred=mapping.is_preferred,
        is_active=mapping.is_active,
        priority=mapping.priority,
    )

def to_model_response(
    model: Model,
) -> ModelResponse:
    return ModelResponse(
        id=str(model.id) if model.id else "",
        scope=model.scope,
        display_name=model.display_name,
        vendor=model.vendor,
        type=model.type,
        billing_ratio=model.billing_ratio,
        support_thinking=model.support_thinking,
        support_vision=model.support_vision,
        support_tools=model.support_tools,
        support_streaming=model.support_streaming,
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        is_active=model.is_active,
        mappings=None,
    )

def to_model_response_with_mapping(
    model_info: ModelInfo,
    providers: Dict[str, Provider] = None,
) -> ModelResponse:
    model_response = to_model_response(model_info.model)
    model_response.mappings = [
        to_mapping_response(mapping, providers)
        for mapping in model_info.mappings
    ]
    return model_response


@router.get("/listAvailableModels", response_model=R[AvailableModelsResponse])
@inject
async def list_available_models(
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    system_model_infos = await model_repo.list_models_and_mappings(None)

    user_model_infos = await model_repo.list_models_and_mappings(user_id)
    user_providers = await provider_repo.list_providers(user_id)
    user_providers = {
        str(provider.id): provider
        for provider in user_providers
        if provider.id is not None
    }

    return R.success(data=AvailableModelsResponse(
        system_models=[
            to_model_response_with_mapping(model_info)
            for model_info in system_model_infos
            if model_info.model.is_active
        ],
        user_models=[
            to_model_response_with_mapping(model_info, user_providers)
            for model_info in user_model_infos
            if model_info.model.is_active
        ],
    ))


@router.get("/listUserProviders", response_model=R[ListUserProvidersResponse])
@inject
async def list_user_providers(
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    providers = await provider_repo.list_providers(user_id)
    return R.success(data=ListUserProvidersResponse(
        providers=[to_provider_response(provider) for provider in providers],
    ))


@router.post("/createUserProvider", response_model=R, status_code=200)
@inject
async def create_user_provider(
    req: CreateUserProviderRequest,
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    await provider_repo.create_provider(
        Provider(
            name=req.name,
            api_base_url=req.api_base_url,
            api_key=req.api_key,
            type=req.type,
        )
    , user_id)
    return R.success()


@router.post("/updateUserProvider", response_model=R, status_code=200)
@inject
async def update_user_provider(
    req: UpdateUserProviderRequest,
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    provider_id = PydanticObjectId(req.provider_id)
    updates = req.model_dump(exclude={"provider_id"}, exclude_unset=True)
    await provider_repo.update_provider(provider_id, updates, user_id)
    return R.success()


@router.post("/deleteUserProvider", response_model=R, status_code=200)
@inject
async def delete_user_provider(
    req: DeleteUserProviderRequest,
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    provider_id = PydanticObjectId(req.provider_id)
    await provider_repo.remove_provider(provider_id, user_id)
    return R.success()


@router.get("/listUserModelsByProviderId", response_model=R[ListUserModelsResponse])
@inject
async def list_user_models_by_provider_id(
    provider_id: str = Query(..., description="Provider ID"),
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_infos = await model_repo.list_models_by_provider_id(
        PydanticObjectId(provider_id),
        user_id,
    )
    return R.success(data=ListUserModelsResponse(
        models=[
            to_model_response(model_info)
            for model_info in model_infos
        ],
    ))

@router.get("/listAllUserModels", response_model=R[ListUserModelsResponse])
@inject
async def list_all_user_models(
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_infos = await model_repo.list_models_and_mappings(user_id)
    return R.success(data=ListUserModelsResponse(
        models=[
            to_model_response(model_info)
            for model_info in model_infos
        ],
    ))


@router.post("/createUserModel", response_model=R, status_code=200)
@inject
async def create_user_model(
    req: CreateUserModelRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.create_model(
        Model(
            display_name=req.display_name,
            vendor=req.vendor,
            type=req.type,
            billing_ratio=req.billing_ratio,
            support_thinking=req.support_thinking,
            support_vision=req.support_vision,
            support_tools=req.support_tools,
            support_streaming=req.support_streaming,
            context_window_tokens=req.context_window_tokens,
            max_output_tokens=req.max_output_tokens,
        )
    , user_id)
    return R.success()


@router.post("/updateUserModel", response_model=R, status_code=200)
@inject
async def update_user_model(
    req: UpdateUserModelRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_id = PydanticObjectId(req.model_id)
    updates = req.model_dump(exclude={"model_id"}, exclude_unset=True)
    await model_repo.update_model(model_id, updates, user_id)
    return R.success()


@router.post("/deleteUserModel", response_model=R, status_code=200)
@inject
async def delete_user_model(
    req: DeleteUserModelRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_id = PydanticObjectId(req.model_id)
    await model_repo.delete_model(model_id, user_id)
    return R.success()


@router.post("/bindModelProvider", response_model=R, status_code=200)
@inject
async def bind_model_provider(
    req: BindModelProviderRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.bind_model_to_provider(
        PydanticObjectId(req.model_id),
        PydanticObjectId(req.provider_id),
        req.provider_model_name,
        user_id,
        is_preferred=req.is_preferred,
        is_active=req.is_active,
    )
    return R.success()


@router.post("/unbindModelProvider", response_model=R, status_code=200)
@inject
async def unbind_model_provider(
    req: UnbindModelProviderRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.unbind_model_from_provider(PydanticObjectId(req.model_id), PydanticObjectId(req.provider_id), user_id)
    return R.success()
