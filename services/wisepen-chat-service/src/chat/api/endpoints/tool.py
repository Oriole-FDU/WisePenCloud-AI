from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from chat.api.schemas.tool import (
    DeleteUserToolConfigRequest,
    ListUserToolsResponse,
    ToolResponse,
    UpdateUserToolConfigRequest,
)
from chat.application.tools.core import Tool, ToolRegistry
from chat.container import Container
from chat.domain.entities.tool_config import UserToolConfig
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import ToolConfigRepository
from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import require_login


router = APIRouter()

def _build_tool_response(tool: Tool, entity: UserToolConfig | None) -> ToolResponse:
    definition = tool.definition
    config_spec = definition.config_spec
    if config_spec is None:
        return ToolResponse(
            name=definition.llm_spec.name,
            description=definition.llm_spec.description,
            requires_config=False,
            configured=True,
            enabled=True,
        )

    # 检查用户是否有配置、必填项是否完整
    configured = False
    missing_keys: list[str] = []
    if entity is not None:
        for key in config_spec.required_keys:
            source = entity.secret_config if key in config_spec.secret_keys else entity.config
            if source.get(key) is None or (isinstance(source.get(key), str) and not source.get(key).strip()):
                missing_keys.append(key)
        configured = not missing_keys

    return ToolResponse(
        name=definition.llm_spec.name,
        description=definition.llm_spec.description,
        requires_config=True,
        configured=configured,
        enabled=entity.enabled if entity is not None else True,
        missing_config_keys=missing_keys,
        config_schema=dict(config_spec.schema),
        secret_fingerprints={
            key: value
            for key, value in (entity.secret_fingerprints if entity is not None else {}).items()
            if key in config_spec.secret_keys
        },
    )

@router.get(
    "/listUserTools",
    response_model=R[ListUserToolsResponse],
    summary="查询用户 Tool",
    description="""
- 用途：为前端 Tool 配置页查询当前用户可管理的 Tool 及配置状态。
- 请求：无业务请求参数，用户身份来自请求上下文。
- 约束：当前用户必须已登录。
- 处理：遍历已注册 Tool，合并当前用户保存的 Tool 配置，计算是否需要配置、是否已配置、是否启用、缺失配置项、配置 schema 和密钥指纹；不返回密钥明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN。
- 响应：返回当前用户的 Tool 列表及各 Tool 的配置状态。
""",
)
@inject
async def list_user_tools(
    user_id: str = Depends(require_login),
    tool_registry: ToolRegistry = Depends(Provide[Container.tool_registry]),
    tool_config_repo: ToolConfigRepository = Depends(Provide[Container.tool_config_repo]),
):
    configs = await tool_config_repo.list_tool_configs(user_id)
    configs = { config.tool_name: config for config in configs}

    return R.success(data=ListUserToolsResponse(
        tools=[
            _build_tool_response(tool, configs.get(name))
            for name, tool in sorted(tool_registry.tools().items(), key=lambda item: item[0])
        ],
    ))


@router.get(
    "/getUserToolConfig",
    response_model=R[ToolResponse],
    summary="查询用户 Tool 配置",
    description="""
- 用途：查询当前用户某个 Tool 的配置状态，用于配置页详情展示。
- 请求：tool_name 指定目标 Tool。
- 约束：当前用户必须已登录；目标 Tool 必须已注册。
- 处理：读取目标 Tool 定义和当前用户保存的配置，计算配置完整性和缺失配置项；不返回密钥明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Tool 不存在 -> ChatErrorCode.TOOL_NOT_FOUND。
- 响应：返回目标 Tool 的配置状态、配置 schema 和密钥指纹。
""",
)
@inject
async def get_user_tool_config(
    tool_name: str = Query(..., description="Tool name"),
    user_id: str = Depends(require_login),
    tool_registry: ToolRegistry = Depends(Provide[Container.tool_registry]),
    tool_config_repo: ToolConfigRepository = Depends(Provide[Container.tool_config_repo]),
):
    tool = tool_registry.tools().get(tool_name)
    if tool is None:
        raise ServiceException(ChatErrorCode.TOOL_NOT_FOUND)
    config = await tool_config_repo.get_tool_config(user_id, tool_name)
    return R.success(data=_build_tool_response(tool, config))


@router.post(
    "/updateUserToolConfig",
    response_model=R[ToolResponse],
    status_code=200,
    summary="更新用户 Tool 配置",
    description="""
- 用途：维护当前用户某个可配置 Tool 的启用状态和配置内容。
- 请求：tool_name 指定目标 Tool；enabled 未传时沿用原值，新建时默认启用；config 传普通配置；secret_config 传密钥类配置。
- 约束：当前用户必须已登录；目标 Tool 必须已注册且声明 config_spec；普通配置和密钥配置只能包含 config_spec 声明的字段。
- 处理：合并已有配置和本次传入字段，保存用户级 Tool 配置、schema 版本和密钥指纹；不校验外部服务连通性，不返回密钥明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Tool 不存在 -> ChatErrorCode.TOOL_NOT_FOUND；Tool 不支持配置或配置字段不合法 -> ChatErrorCode.TOOL_CONFIG_INVALID；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回更新后的 Tool 配置状态。
""",
)
@inject
async def update_user_tool_config(
    req: UpdateUserToolConfigRequest,
    user_id: str = Depends(require_login),
    tool_registry: ToolRegistry = Depends(Provide[Container.tool_registry]),
    tool_config_repo: ToolConfigRepository = Depends(Provide[Container.tool_config_repo]),
):
    tool = tool_registry.tools().get(req.tool_name)
    if tool is None:
        raise ServiceException(ChatErrorCode.TOOL_NOT_FOUND)

    # 工具本身需要可配置
    config_spec = tool.definition.config_spec
    if config_spec is None:
        raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, "tool does not support user config")


    property_names = set(config_spec.schema.get("properties") or {})
    secret_names = set(config_spec.secret_keys)

    if req.config is not None:
        if not isinstance(req.config, dict):
            raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, "config must be an object")
        # 排除未知参数
        unknown = set(req.config) - property_names
        if unknown:
            raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, f"unknown config keys: {sorted(unknown)}")

        # 保密字段不能出现在 config 中
        secret_in_config = set(req.config) & secret_names
        if secret_in_config:
            raise ServiceException(
                ChatErrorCode.TOOL_CONFIG_INVALID,
                f"secret keys must be passed in secret_config: {sorted(secret_in_config)}",
            )

    if req.secret_config is not None:
        if not isinstance(req.secret_config, dict):
            raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, "secret_config must be an object")
        # 排除未知保密参数
        unknown_secret = set(req.secret_config) - secret_names
        if unknown_secret:
            raise ServiceException(
                ChatErrorCode.TOOL_CONFIG_INVALID,f"unknown secret config keys: {sorted(unknown_secret)}")

        # 保密字段不能为空
        invalid_secret = [key for key, value in req.secret_config.items() if not isinstance(value, str) or not value.strip()]
        if invalid_secret:
            raise ServiceException(
                ChatErrorCode.TOOL_CONFIG_INVALID,
                f"secret config values must be non-blank strings: {sorted(invalid_secret)}",
            )

    existing = await tool_config_repo.get_tool_config(user_id, req.tool_name) # 是否已存在配置

    config = dict(existing.config if existing is not None else {})
    if req.config is not None: config.update(req.config)
    secret_config = dict(existing.secret_config if existing is not None else {})
    if req.secret_config is not None: secret_config.update(req.secret_config)


    entity = await tool_config_repo.upsert_tool_config(
        user_id=user_id,
        tool_name=req.tool_name,
        enabled=req.enabled if req.enabled is not None else (existing.enabled if existing is not None else True),
        config=config,
        secret_config=secret_config,
        secret_fingerprints={
            key: "*" * len(value) if len(value) <= 8 else f"{value[:4]}***{value[-4:]}"
            for key, value in secret_config.items()
        },
        schema_version=config_spec.version,
    )
    return R.success(data=_build_tool_response(tool, entity))


@router.post(
    "/deleteUserToolConfig",
    response_model=R,
    status_code=200,
    summary="删除用户 Tool 配置",
    description="""
- 用途：清除当前用户某个 Tool 的用户级配置。
- 请求：tool_name 指定目标 Tool。
- 约束：当前用户必须已登录；目标 Tool 必须已注册。
- 处理：删除当前用户保存的目标 Tool 配置；不删除 Tool 定义，也不影响其他用户配置。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Tool 不存在 -> ChatErrorCode.TOOL_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_user_tool_config(
    req: DeleteUserToolConfigRequest,
    user_id: str = Depends(require_login),
    tool_registry: ToolRegistry = Depends(Provide[Container.tool_registry]),
    tool_config_repo: ToolConfigRepository = Depends(Provide[Container.tool_config_repo]),
):
    tool = tool_registry.tools().get(req.tool_name)
    if tool is None:
        raise ServiceException(ChatErrorCode.TOOL_NOT_FOUND)
    await tool_config_repo.delete_tool_config(user_id, req.tool_name)
    return R.success()
