from typing import Annotated, Any

from beanie import PydanticObjectId
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request

from chat.api.admin_route import AdminRoute
from chat.api.converters.admin_model_converter import to_provider_response, to_model_response
from chat.api.schemas.admin_model import (
    AdminModelResponse, AdminProviderResponse, AdminRequest, AdminWriteResponse,
    CreateModelMappingRequest, CreateModelRequest, CreateProviderRequest, MappingListResponse,
    ProviderDetailResponse, RotateProviderApiKeyRequest, SetPreferredMappingRequest,
    UpdateModelMappingRequest, UpdateModelRequest, UpdateProviderRequest,
)
from chat.application.admin_model_management import AdminActor, AdminModelManagement, AdminWriteResult
from chat.domain.entities.model import ModelFamily
from chat.domain.entities.provider import ProviderType
from chat.domain.model_configuration import PROVIDER_MODEL_FAMILIES
from chat.service_client.system_service_client import SystemClient
from common.core.domain import IdentityType, PageResult, R
from common.security import require_role


require_admin = require_role(IdentityType.ADMIN)


@inject
def _bind_operation_logger(
    request: Request,
    client: SystemClient = Depends(Provide["system_client"]),
) -> None:
    if request.method == "POST":
        request.state.admin_log_client = client


router = APIRouter(
    route_class=AdminRoute,
    dependencies=[Depends(require_admin), Depends(_bind_operation_logger)],
)


# 由 main.py 统一 wiring；字符串标识避免导入路由时加载容器的 Nacos 配置。
Management = Annotated[AdminModelManagement, Depends(Provide["admin_model_management"])]
ActorId = Annotated[str, Depends(require_admin)]
Page = Annotated[int, Query(ge=1)]
Size = Annotated[int, Query(ge=1, le=100)]


def _build_actor(request: Request, user_id: str, reason: str) -> AdminActor:
    return AdminActor(user_id=user_id, reason=reason, request_id=request.state.admin_request_id)


def _editable_fields(req: AdminRequest, *excluded: str) -> dict[str, Any]:
    return req.model_dump(exclude={"reason", *excluded}, exclude_unset=True)


def _write_response(result: AdminWriteResult, request: Request) -> R[AdminWriteResponse]:
    request.state.admin_operation = result.log_fields
    return R.success(AdminWriteResponse.model_validate(result, from_attributes=True))


@router.get(
    "/model/getMetadata", response_model=R[dict], summary="管理员：查询模型配置选项",
    description="""
- 用途：为管理表单提供供应商类型、模型协议族及兼容组合。
- 请求：无业务请求参数；当前用户必须具有平台 ADMIN 身份。
- 响应：返回 provider_types、model_families 和 compatible_families。
""",
)
async def get_metadata() -> R[dict]:
    return R.success({
        "provider_types": list(ProviderType),
        "model_families": list(ModelFamily),
        "compatible_families": PROVIDER_MODEL_FAMILIES,
    })


@router.get(
    "/model/listProviders", response_model=R[PageResult[AdminProviderResponse]], summary="管理员：查询系统供应商",
    description="""
- 用途：按名称、类型和启停状态分页选择系统供应商。
- 请求：keyword、type、is_active、page、size；默认包含停用项，每页最多 100 条。
- 约束：仅平台 ADMIN 可读；只返回 SYSTEM/owner=null 的资源。
- 响应：返回供应商 ID、脱敏配置、用量和配置版本，不返回 API Key。
""",
)
@inject
async def list_providers(
    service: Management,
    page: Page = 1,
    size: Size = 20,
    keyword: str | None = Query(default=None, max_length=200),
    provider_type: ProviderType | None = Query(default=None, alias="type"),
    is_active: bool | None = None,
) -> R[PageResult[AdminProviderResponse]]:
    filters = {k: v for k, v in {"type": provider_type, "is_active": is_active}.items() if v is not None}
    values, total = await service.list_providers(page=page, size=size, keyword=keyword, filters=filters)
    return R.success(PageResult.of([to_provider_response(value) for value in values], total, page, size))


@router.get(
    "/model/getProvider", response_model=R[ProviderDetailResponse], summary="管理员：查询供应商详情及关联模型",
    description="""
- 请求：provider_id。
- 约束：仅平台 ADMIN 可读；个人资源按不存在处理。
- 失败：系统供应商不存在返回 404。
- 响应：返回脱敏的 provider 和关联 models，包含配置版本和可用性。
""",
)
@inject
async def get_provider(provider_id: PydanticObjectId, service: Management) -> R[ProviderDetailResponse]:
    provider, models = await service.get_provider(provider_id)
    return R.success(ProviderDetailResponse(
        provider=to_provider_response(provider),
        models=[to_model_response(model) for model in models],
    ))


@router.post(
    "/model/createProvider", response_model=R[AdminWriteResponse], summary="管理员：创建停用的系统供应商",
    description="""
- 请求：name、type、api_key、可选 base_url 和必填修改原因 reason。
- 约束：仅平台 ADMIN 可写；固定 SYSTEM/owner=null，默认停用。
- 处理：事务提交配置成功后发送脱敏操作日志。
- 失败：名称重复返回 409，不支持事务返回 503。
- 响应：返回资源 ID、配置版本及 request_id，不返回 API Key。
""",
)
@inject
async def create_provider(
    req: CreateProviderRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.create_provider(_editable_fields(req), _build_actor(request, user_id, req.reason))
    return _write_response(result, request)


@router.post(
    "/model/updateProvider", response_model=R[AdminWriteResponse], summary="管理员：编辑或启停供应商",
    description="""
- 请求：provider_id、expected_version、reason，以及 name/base_url/is_active 中待修改的字段。
- 约束：仅平台 ADMIN 可写；不允许编辑类型或 API Key，未传字段保持原值。
- 处理：按版本更新，启停同步更新关联模型版本并保护默认模型的最后一条可用路径；提交成功后记录日志。
- 失败：不存在返回 404，版本过期、名称重复或默认路径不可用返回 409。
- 响应：返回新配置版本；前端应刷新供应商及关联模型详情。
""",
)
@inject
async def update_provider(
    req: UpdateProviderRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.update_provider(
        req.provider_id, req.expected_version,
        _editable_fields(req, "provider_id", "expected_version"),
        _build_actor(request, user_id, req.reason),
    )
    return _write_response(result, request)


@router.post(
    "/model/rotateProviderApiKey", response_model=R[AdminWriteResponse], summary="管理员：更换供应商 API Key",
    description="""
- 请求：provider_id、expected_version、新 api_key 和修改原因 reason。
- 约束：仅平台 ADMIN 可写；拒绝空白密钥，不在响应和日志中输出明文。
- 处理：按版本替换凭证，提交成功后记录脱敏日志；下一次模型解析时生效。
- 失败：不存在返回 404，版本过期返回 409，不支持事务返回 503。
- 响应：返回新配置版本和 request_id；保存后重新查询详情。
""",
)
@inject
async def rotate_key(
    req: RotateProviderApiKeyRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.rotate_provider_key(
        req.provider_id, req.expected_version, req.api_key,
        _build_actor(request, user_id, req.reason),
    )
    return _write_response(result, request)


@router.get(
    "/model/listModels", response_model=R[PageResult[AdminModelResponse]], summary="管理员：查询系统模型",
    description="""
- 请求：keyword、model_family、is_active、page、size；默认包含停用项，每页最多 100 条。
- 约束：仅平台 ADMIN 可读；只返回 SYSTEM/owner=null 的资源及系统映射。
- 处理：按展示名称搜索，批量读取映射和供应商。
- 响应：返回模型 ID、能力、配置版本、映射及可用性。
""",
)
@inject
async def list_models(
    service: Management,
    page: Page = 1,
    size: Size = 20,
    keyword: str | None = Query(default=None, max_length=200),
    model_family: ModelFamily | None = None,
    is_active: bool | None = None,
) -> R[PageResult[AdminModelResponse]]:
    filters = {k: v for k, v in {"model_family": model_family, "is_active": is_active}.items() if v is not None}
    values, total = await service.list_models(page=page, size=size, keyword=keyword, filters=filters)
    return R.success(PageResult.of([to_model_response(value) for value in values], total, page, size))


@router.get(
    "/model/getModel", response_model=R[AdminModelResponse], summary="管理员：查询系统模型详情",
    description="""
- 请求：model_id，通常从 listModels 的结果中选取。
- 约束：仅平台 ADMIN 可读；个人资源按不存在处理。
- 失败：系统模型不存在返回 404。
- 响应：返回模型、系统映射、配置版本和可用性，不返回供应商密钥。
""",
)
@inject
async def get_model(model_id: PydanticObjectId, service: Management) -> R[AdminModelResponse]:
    return R.success(to_model_response(await service.get_model(model_id)))


@router.post(
    "/model/createModel", response_model=R[AdminWriteResponse], summary="管理员：创建停用的系统模型",
    description="""
- 请求：display_name、协议族、能力、可选 token 上限及必填修改原因 reason。
- 约束：仅平台 ADMIN 可写；输出上限必须小于上下文窗口。
- 处理：创建停用模型，提交成功后记录日志；配置可用映射后再启用。
- 失败：参数错误返回 400，名称重复返回 409，不支持事务返回 503。
- 响应：返回模型 ID、配置版本和 request_id。
""",
)
@inject
async def create_model(
    req: CreateModelRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.create_model(_editable_fields(req), _build_actor(request, user_id, req.reason))
    return _write_response(result, request)


@router.post(
    "/model/updateModel", response_model=R[AdminWriteResponse], summary="管理员：编辑或启停系统模型",
    description="""
- 请求：model_id、expected_version、reason，以及待修改的名称、协议族、能力、上限或启停状态。
- 约束：仅平台 ADMIN 可写；启用要求至少一条可用系统映射，默认模型不可停用。
- 处理：校验已有映射协议兼容性，按版本更新模型，提交成功后记录日志。
- 失败：不存在返回 404，版本过期、名称重复或默认模型不可用返回 409。
- 响应：返回新配置版本和 request_id。
""",
)
@inject
async def update_model(
    req: UpdateModelRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.update_model(
        req.model_id, req.expected_version,
        _editable_fields(req, "model_id", "expected_version"),
        _build_actor(request, user_id, req.reason),
    )
    return _write_response(result, request)


@router.get(
    "/model/listModelMappings", response_model=R[MappingListResponse], summary="管理员：查询模型供应商映射",
    description="""
- 请求：model_id。
- 约束：仅平台 ADMIN 可读；只返回系统映射，包含停用项。
- 失败：系统模型不存在返回 404。
- 响应：返回 model_id、模型 config_version 和 mappings；后续映射编辑携带此版本。
""",
)
@inject
async def list_mappings(model_id: PydanticObjectId, service: Management) -> R[MappingListResponse]:
    model = to_model_response(await service.get_model(model_id))
    return R.success(MappingListResponse(
        model_id=model.id,
        config_version=model.config_version,
        mappings=model.mappings,
    ))


@router.post(
    "/model/createModelMapping", response_model=R[AdminWriteResponse], summary="管理员：创建系统模型映射",
    description="""
- 请求：model_id、provider_id、所属模型 expected_version、provider_model_name、倍率、优先级和 reason。
- 约束：仅平台 ADMIN 可写；模型与供应商必须为系统资源且协议兼容。
- 处理：在同一事务中创建映射、递增模型及供应商版本，提交成功后记录日志。
- 失败：不存在返回 404，重复映射、版本过期或默认路径不可用返回 409。
- 响应：返回映射 ID、所属模型的新版本及 request_id；前端应刷新关联详情。
""",
)
@inject
async def create_mapping(
    req: CreateModelMappingRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.create_mapping(
        req.model_id, req.provider_id, req.expected_version,
        _editable_fields(req, "model_id", "provider_id", "expected_version"),
        _build_actor(request, user_id, req.reason),
    )
    return _write_response(result, request)


@router.post(
    "/model/updateModelMapping", response_model=R[AdminWriteResponse], summary="管理员：编辑或启停系统模型映射",
    description="""
- 请求：mapping_id、所属模型 expected_version、reason，以及待修改的模型名、倍率、优先级或启停状态。
- 约束：仅平台 ADMIN 可写；不能改变绑定的模型或供应商。
- 处理：停用时取消首选，保护默认模型最后一条可用路径；递增模型及供应商版本，提交成功后记录日志。
- 失败：不存在返回 404，版本过期或默认路径不可用返回 409。
- 响应：返回所属模型的新版本及 request_id。
""",
)
@inject
async def update_mapping(
    req: UpdateModelMappingRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.update_mapping(
        req.mapping_id, req.expected_version,
        _editable_fields(req, "mapping_id", "expected_version"),
        _build_actor(request, user_id, req.reason),
    )
    return _write_response(result, request)


@router.post(
    "/model/setPreferredMapping", response_model=R[AdminWriteResponse], summary="管理员：切换首选模型映射",
    description="""
- 请求：model_id、mapping_id、所属模型 expected_version 和 reason。
- 约束：仅平台 ADMIN 可写；目标映射必须属于该模型，且映射与供应商均可用。
- 处理：同一事务内清除旧首选、设置新首选、递增模型及供应商版本，提交成功后记录日志。
- 失败：不存在返回 404，不可用返回 400，版本过期或唯一性冲突返回 409。
- 响应：返回所属模型的新版本及 request_id。
""",
)
@inject
async def set_preferred(
    req: SetPreferredMappingRequest, request: Request, service: Management, user_id: ActorId,
) -> R[AdminWriteResponse]:
    result = await service.set_preferred(
        req.model_id, req.mapping_id, req.expected_version,
        _build_actor(request, user_id, req.reason),
    )
    return _write_response(result, request)
