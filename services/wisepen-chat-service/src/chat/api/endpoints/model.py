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
    ListModelsResponse,
    ListUserProvidersResponse,
    ModelProviderMappingResponse,
    ModelResponse,
    ProviderResponse,
    UnbindModelProviderRequest,
    UpdateUserModelRequest,
    UpdateUserProviderRequest,
)
from chat.container import Container
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.domain.entities.model import Model, ModelProviderMapping, ModelScope
from chat.domain.entities.provider import Provider, ProviderScope
from chat.domain.repositories import ModelRepository, ProviderRepository
from chat.domain.repositories.model_repo import ModelInfo
from common.core.domain import R
from common.security import require_login

router = APIRouter()


def to_provider_response(provider: Provider) -> ProviderResponse:
    return ProviderResponse(
        id=str(provider.id) if provider.id else "",
        name=provider.name,
        base_url=provider.base_url,
        api_key_fingerprint=provider.api_key_fingerprint,
        scope=provider.scope,
        type=provider.type,
        is_active=provider.is_active,
        token_usage=provider.token_usage,
        billable_token_usage=provider.billable_token_usage,
    )


def to_mapping_response(
    mapping: ModelProviderMapping,
    provider: Provider | None,
    llm_provider_resolver: LLMProviderResolver,
) -> ModelProviderMappingResponse:
    # 系统提供者不能显示提供者名称
    return ModelProviderMappingResponse(
        model_id=str(mapping.model_id),
        provider_id=str(mapping.provider_id),
        provider_name=provider.name if provider is not None and provider.scope != ProviderScope.SYSTEM else None,
        provider_model_name=mapping.provider_model_name,
        support_runtime_options=llm_provider_resolver.runtime_options_manifest(provider.type) if provider else {},
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
        model_family=model.model_family,
        billing_ratio=model.billing_ratio,
        support_thinking=model.support_thinking,
        support_vision=model.support_vision,
        support_tools=model.support_tools,
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        is_active=model.is_active,
        mappings=None,
    )

def to_model_response_with_mapping(
    model_info: ModelInfo,
    providers: Dict[str, Provider] | None,
    llm_provider_resolver: LLMProviderResolver,
) -> ModelResponse:
    model_response = to_model_response(model_info.model)
    model_response.mappings = [
        to_mapping_response(
            mapping=mapping,
            provider=providers.get(str(mapping.provider_id), None),
            llm_provider_resolver=llm_provider_resolver,
        )
        for mapping in model_info.mappings
    ]
    return model_response


@router.get(
    "/listAvailableModels",
    response_model=R[AvailableModelsResponse],
    summary="查询可用模型",
    description="""
- 用途：查询当前用户可用于发起聊天的系统模型和个人模型。
- 请求：无业务请求参数，用户身份来自请求上下文。
- 约束：当前用户必须已登录；模型、Provider 和映射数据必须可读取。
- 处理：分别读取系统模型、用户模型及其 Provider 映射；系统模型会合并当前用户的个人 Provider 映射，只返回 active 模型，并补充 provider runtime options；系统 Provider 不展示 provider_name。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN。
- 响应：返回系统模型列表和用户模型列表，每个模型包含可用映射信息。
""",
)
@inject
async def list_available_models(
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
    llm_provider_resolver: LLMProviderResolver = Depends(Provide[Container.llm_provider_resolver]),
):
    system_model_infos = await model_repo.list_models_and_mappings(ModelScope.SYSTEM, user_id)
    system_providers = await provider_repo.list_providers(None)
    system_providers = {
        str(provider.id): provider
        for provider in system_providers
        if provider.id is not None
    }

    user_model_infos = await model_repo.list_models_and_mappings(ModelScope.USER, user_id)
    user_providers = await provider_repo.list_providers(user_id)
    user_providers = {
        str(provider.id): provider
        for provider in user_providers
        if provider.id is not None
    }

    return R.success(data=AvailableModelsResponse(
        system_models=[
            to_model_response_with_mapping(model_info, {**system_providers, **user_providers}, llm_provider_resolver)
            for model_info in system_model_infos
            if model_info.model.is_active
        ],
        user_models=[
            to_model_response_with_mapping(model_info, user_providers, llm_provider_resolver)
            for model_info in user_model_infos
            if model_info.model.is_active
        ],
    ))


@router.get(
    "/listUserProviders",
    response_model=R[ListUserProvidersResponse],
    summary="查询用户 Provider",
    description="""
- 用途：查询当前用户维护的个人 LLM Provider 列表。
- 请求：无业务请求参数，用户身份来自请求上下文。
- 约束：当前用户必须已登录。
- 处理：读取当前用户的 Provider 并返回脱敏后的 API Key 指纹；不返回明文 API Key。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN。
- 响应：返回当前用户 Provider 列表。
""",
)
@inject
async def list_user_providers(
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    providers = await provider_repo.list_providers(user_id)
    return R.success(data=ListUserProvidersResponse(
        providers=[to_provider_response(provider) for provider in providers],
    ))


@router.post(
    "/createUserProvider",
    response_model=R,
    status_code=200,
    summary="创建用户 Provider",
    description="""
- 用途：为当前用户新增一个个人 LLM Provider。
- 请求：name、type、api_key 描述 Provider；base_url 可选指定自定义网关；is_active 控制是否启用。
- 约束：当前用户必须已登录；同一用户下 Provider 名称不能重复；请求参数必须满足 schema 约束。
- 处理：创建归属于当前用户的 Provider，保存 API Key 并生成指纹；不自动绑定模型。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Provider 已存在 -> ChatErrorCode.PROVIDER_ALREADY_EXISTS；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def create_user_provider(
    req: CreateUserProviderRequest,
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    await provider_repo.create_provider(
        Provider(
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
            type=req.type,
            is_active=req.is_active,
        )
    , user_id)
    return R.success()


@router.post(
    "/updateUserProvider",
    response_model=R,
    status_code=200,
    summary="更新用户 Provider",
    description="""
- 用途：维护当前用户的个人 LLM Provider 配置。
- 请求：provider_id 指定目标 Provider；name、base_url、api_key、type、is_active 未传时不更新对应字段。
- 约束：当前用户必须已登录；目标 Provider 必须属于当前用户；更新后的 Provider 名称不能与同用户其他 Provider 冲突。
- 处理：按传入字段更新 Provider；不直接修改模型或模型映射。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Provider 不存在或不属于当前用户 -> ChatErrorCode.PROVIDER_NOT_FOUND；Provider 名称冲突 -> ChatErrorCode.PROVIDER_ALREADY_EXISTS；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
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


@router.post(
    "/deleteUserProvider",
    response_model=R,
    status_code=200,
    summary="删除用户 Provider",
    description="""
- 用途：删除当前用户的个人 LLM Provider。
- 请求：provider_id 指定目标 Provider。
- 约束：当前用户必须已登录；目标 Provider 必须属于当前用户。
- 处理：删除 Provider 记录；关联映射的约束由 repository 保证。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Provider 不存在或不属于当前用户 -> ChatErrorCode.PROVIDER_NOT_FOUND；Provider 仍被模型映射使用 -> ChatErrorCode.PROVIDER_IN_USE；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_user_provider(
    req: DeleteUserProviderRequest,
    user_id: str = Depends(require_login),
    provider_repo: ProviderRepository = Depends(Provide[Container.provider_repo]),
):
    provider_id = PydanticObjectId(req.provider_id)
    await provider_repo.remove_provider(provider_id, user_id)
    return R.success()


@router.get(
    "/listModelsByProviderId",
    response_model=R[ListModelsResponse],
    summary="按 Provider 查询绑定模型",
    description="""
- 用途：查询当前用户指定 Provider 下关联的系统模型或个人模型。
- 请求：provider_id 指定目标 Provider。
- 约束：当前用户必须已登录；provider_id 必须可转换为有效对象 ID。
- 处理：按 Provider ID 和当前用户查询模型映射并返回模型摘要；系统模型本体只读，不返回 Provider 明文 API Key。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回模型列表。
""",
)
@inject
async def list_models_by_provider_id(
    provider_id: str = Query(..., description="Provider ID"),
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_infos = await model_repo.list_models_by_provider_id(
        PydanticObjectId(provider_id),
        user_id,
    )
    return R.success(data=ListModelsResponse(
        models=[
            to_model_response(model_info.model)
            for model_info in model_infos
        ],
    ))

@router.get(
    "/listModels",
    response_model=R[ListModelsResponse],
    summary="查询模型",
    description="""
- 用途：查询当前用户的全部个人模型和系统模型。
- 请求：无业务请求参数，用户身份来自请求上下文。
- 约束：当前用户必须已登录。
- 处理：读取当前用户模型及映射信息，并转换为模型列表响应。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN。
- 响应：返回当前用户模型列表。
""",
)
@inject
async def list_models(
    model_scope: ModelScope,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_infos = await model_repo.list_models_and_mappings(model_scope, user_id)
    return R.success(data=ListModelsResponse(
        models=[
            to_model_response(model_info.model)
            for model_info in model_infos
        ],
    ))


@router.post(
    "/createUserModel",
    response_model=R,
    status_code=200,
    summary="创建用户模型",
    description="""
- 用途：为当前用户新增一个个人模型定义。
- 请求：display_name、model_family、billing_ratio、能力开关和上下文窗口字段描述模型能力。
- 约束：当前用户必须已登录；同一用户下模型展示名不能重复；请求参数必须满足 schema 约束。
- 处理：创建归属于当前用户的模型定义；不自动创建 Provider 或模型映射。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；模型已存在 -> ChatErrorCode.MODEL_ALREADY_EXISTS；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def create_user_model(
    req: CreateUserModelRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.create_model(
        Model(
            display_name=req.display_name,
            model_family=req.model_family,
            billing_ratio=req.billing_ratio,
            support_thinking=req.support_thinking,
            support_vision=req.support_vision,
            support_tools=req.support_tools,
            context_window_tokens=req.context_window_tokens,
            max_output_tokens=req.max_output_tokens,
        )
    , user_id)
    return R.success()


@router.post(
    "/updateUserModel",
    response_model=R,
    status_code=200,
    summary="更新用户模型",
    description="""
- 用途：维护当前用户的个人模型定义。
- 请求：model_id 指定目标模型；display_name、model_family、billing_ratio、能力开关、上下文窗口和 is_active 未传时不更新对应字段。
- 约束：当前用户必须已登录；目标模型必须属于当前用户；更新后的模型展示名不能与同用户其他模型冲突。
- 处理：按传入字段更新模型定义；不直接修改 Provider 或模型映射。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；模型不存在或不属于当前用户 -> ChatErrorCode.MODEL_NOT_FOUND；模型展示名冲突 -> ChatErrorCode.MODEL_ALREADY_EXISTS；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
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


@router.post(
    "/deleteUserModel",
    response_model=R,
    status_code=200,
    summary="删除用户模型",
    description="""
- 用途：删除当前用户的个人模型定义。
- 请求：model_id 指定目标模型。
- 约束：当前用户必须已登录；目标模型必须属于当前用户。
- 处理：删除模型定义；关联映射的清理由 repository 保证。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；模型不存在或不属于当前用户 -> ChatErrorCode.MODEL_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_user_model(
    req: DeleteUserModelRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    model_id = PydanticObjectId(req.model_id)
    await model_repo.delete_model(model_id, user_id)
    return R.success()


@router.post(
    "/bindModelProvider",
    response_model=R,
    status_code=200,
    summary="绑定模型 Provider",
    description="""
- 用途：为当前用户可访问的模型绑定一个个人 Provider 侧模型名称。
- 请求：model_id 指定系统模型或用户模型；provider_id 指定用户 Provider；provider_model_name 是 Provider 实际模型名；is_preferred 和 is_active 控制映射偏好与启用状态。
- 约束：当前用户必须已登录；模型必须是系统模型或当前用户模型；Provider 必须属于当前用户。
- 处理：创建或更新模型到 Provider 的映射关系；设置 Provider 侧模型名、启用状态和首选状态；不修改模型定义或 Provider 凭证。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；模型不存在或不可访问 -> ChatErrorCode.MODEL_NOT_FOUND；Provider 不存在或不属于当前用户 -> ChatErrorCode.PROVIDER_NOT_FOUND；并发创建映射冲突 -> ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
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


@router.post(
    "/unbindModelProvider",
    response_model=R,
    status_code=200,
    summary="解绑模型 Provider",
    description="""
- 用途：解除当前用户可访问模型与个人 Provider 的绑定关系。
- 请求：model_id 指定系统模型或用户模型；provider_id 指定用户 Provider。
- 约束：当前用户必须已登录；目标映射必须存在且属于当前用户。
- 处理：删除模型到 Provider 的映射关系；不删除模型定义或 Provider。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；模型不存在或不可访问 -> ChatErrorCode.MODEL_NOT_FOUND；模型供应商映射不存在 -> ChatErrorCode.MODEL_MAPPING_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def unbind_model_provider(
    req: UnbindModelProviderRequest,
    user_id: str = Depends(require_login),
    model_repo: ModelRepository = Depends(Provide[Container.model_repo]),
):
    await model_repo.unbind_model_from_provider(PydanticObjectId(req.model_id), PydanticObjectId(req.provider_id), user_id)
    return R.success()
