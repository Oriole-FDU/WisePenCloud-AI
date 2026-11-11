from decimal import Decimal

from beanie import PydanticObjectId
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from chat.api.schemas.model import ListModelsResponse, ModelResponse, ProviderResponse
from chat.api.schemas.model_admin import (
    BindSystemModelProviderRequest,
    CreateSystemModelRequest,
    CreateSystemProviderRequest,
    DeleteSystemModelRequest,
    DeleteSystemProviderRequest,
    ListSystemProvidersResponse,
    UnbindSystemModelProviderRequest,
    UpdateSystemModelRequest,
    UpdateSystemProviderRequest,
)
from chat.container import Container
from chat.domain.entities.model import Model, ModelScope
from chat.domain.entities.provider import Provider, ProviderScope
from chat.domain.repositories import ModelRepository, ProviderRepository
from common.core.domain import IdentityType, R
from common.security import require_role

router = APIRouter()


def _to_provider_response(provider: Provider) -> ProviderResponse:
    return ProviderResponse(
        id=str(provider.id) if provider.id else "",
        name=provider.name,
        base_url=provider.base_url,
        api_key_fingerprint=provider.api_key_fingerprint,
        scope=provider.scope,
        type=provider.type,
        is_active=provider.is_active,
        input_tokens=provider.input_tokens,
        cached_input_tokens=provider.cached_input_tokens,
        output_tokens=provider.output_tokens,
        billable_tokens=provider.billable_tokens,
    )


def _to_model_response(model: Model) -> ModelResponse:
    return ModelResponse(
        id=str(model.id) if model.id else "",
        scope=model.scope,
        display_name=model.display_name,
        model_family=model.model_family,
        support_thinking=model.support_thinking,
        support_vision=model.support_vision,
        support_tools=model.support_tools,
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        is_active=model.is_active,
        mappings=None,
    )


@router.get(
    "/provider/listSystemProviders",
    response_model=R[ListSystemProvidersResponse],
    summary="查询系统 Provider",
)
@inject
async def list_system_providers(
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    providers = await provider_repo.list_providers(ProviderScope.SYSTEM)
    return R.success(
        data=ListSystemProvidersResponse(
            providers=[_to_provider_response(provider) for provider in providers],
        )
    )


@router.post(
    "/provider/createSystemProvider",
    response_model=R,
    summary="创建系统 Provider",
)
@inject
async def create_system_provider(
    req: CreateSystemProviderRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    await provider_repo.create_provider(
        Provider(
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
            type=req.type,
            is_active=req.is_active,
        ),
        scope=ProviderScope.SYSTEM
    )
    return R.success()


@router.post(
    "/provider/updateSystemProvider",
    response_model=R,
    summary="更新系统 Provider",
)
@inject
async def update_system_provider(
    req: UpdateSystemProviderRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    provider_id = PydanticObjectId(req.provider_id)
    updates = req.model_dump(exclude={"provider_id"}, exclude_unset=True)
    await provider_repo.update_provider(provider_id, updates, ProviderScope.SYSTEM)
    return R.success()


@router.post(
    "/provider/deleteSystemProvider",
    response_model=R,
    summary="删除系统 Provider",
)
@inject
async def delete_system_provider(
    req: DeleteSystemProviderRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    await provider_repo.remove_provider(PydanticObjectId(req.provider_id), ProviderScope.SYSTEM)
    return R.success()


@router.get(
    "/model/listSystemModelsByProviderId",
    response_model=R[ListModelsResponse],
    summary="按系统 Provider 查询系统模型",
)
@inject
async def list_system_models_by_provider_id(
    provider_id: str = Query(..., description="系统 Provider ID"),
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    system_provider_id = PydanticObjectId(provider_id)
    await provider_repo.get_provider(system_provider_id, ProviderScope.SYSTEM)
    model_infos = await model_repo.list_models_by_provider_id(system_provider_id, only_system_model=True)
    return R.success(
        data=ListModelsResponse(
            models=[_to_model_response(info.model) for info in model_infos],
        )
    )


@router.get(
    "/model/listSystemModels",
    response_model=R[ListModelsResponse],
    summary="查询系统模型",
)
@inject
async def list_system_models(
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_infos = await model_repo.list_models_and_mappings(ModelScope.SYSTEM, None)
    return R.success(
        data=ListModelsResponse(
            models=[_to_model_response(info.model) for info in model_infos],
        )
    )


@router.post(
    "/model/createSystemModel",
    response_model=R,
    summary="创建系统模型",
)
@inject
async def create_system_model(
    req: CreateSystemModelRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.create_model(
        Model(
            display_name=req.display_name,
            model_family=req.model_family,
            support_thinking=req.support_thinking,
            support_vision=req.support_vision,
            support_tools=req.support_tools,
            context_window_tokens=req.context_window_tokens,
            max_output_tokens=req.max_output_tokens,
        ),
        scope=ModelScope.SYSTEM,
    )
    return R.success()


@router.post(
    "/model/updateSystemModel",
    response_model=R,
    summary="更新系统模型",
)
@inject
async def update_system_model(
    req: UpdateSystemModelRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_id = PydanticObjectId(req.model_id)
    updates = req.model_dump(exclude={"model_id"}, exclude_unset=True)
    await model_repo.update_model(model_id, updates, ModelScope.SYSTEM)
    return R.success()


@router.post(
    "/model/deleteSystemModel",
    response_model=R,
    summary="删除系统模型",
    description="""
- 用途：删除系统模型及其全部供应商映射（含用户基于该系统模型的覆盖映射）。
- 请求：model_id 指定目标系统模型。
- 约束：目标必须是 SYSTEM 作用域模型。
- 处理：级联删除该系统模型的系统映射与所有用户覆盖映射，再删除模型本体；如需保留历史可改用 updateSystemModel 停用。
- 失败：模型不存在 -> ChatErrorCode.MODEL_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_system_model(
    req: DeleteSystemModelRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.delete_model(PydanticObjectId(req.model_id), ModelScope.SYSTEM)
    return R.success()


@router.post(
    "/model/bindSystemModelProvider",
    response_model=R,
    summary="绑定系统模型与 Provider",
)
@inject
async def bind_system_model_provider(
    req: BindSystemModelProviderRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_id = PydanticObjectId(req.model_id)
    provider_id = PydanticObjectId(req.provider_id)
    await model_repo.bind_model_to_provider(
        model_id,
        provider_id,
        req.provider_model_name,
        only_system_model=True,
        owner_user_id=None,
        input_billing_ratio=Decimal(req.input_billing_ratio),
        cached_input_billing_ratio=Decimal(req.cached_input_billing_ratio),
        output_billing_ratio=Decimal(req.output_billing_ratio),
        is_preferred=req.is_preferred,
        is_active=req.is_active,
    )
    return R.success()


@router.post(
    "/model/unbindSystemModelProvider",
    response_model=R,
    summary="解绑系统模型与 Provider",
)
@inject
async def unbind_system_model_provider(
    req: UnbindSystemModelProviderRequest,
    _admin_id: str = Depends(require_role(IdentityType.ADMIN)),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.unbind_model_from_provider(
        PydanticObjectId(req.model_id),
        PydanticObjectId(req.provider_id),
        only_system_model=True,
        owner_user_id=None
    )
    return R.success()
