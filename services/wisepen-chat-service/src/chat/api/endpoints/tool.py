import re
import uuid

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from chat.api.schemas.tool import (
    DeleteUserToolConfigRequest,
    DeleteUserMcpServerRequest,
    ListUserToolsResponse,
    ListUserMcpServersResponse,
    McpToolSnapshotResponse,
    PreviewUserMcpServerRequest,
    PreviewUserMcpServerResponse,
    ToolResponse,
    UpdateUserToolConfigRequest,
    UpsertUserMcpServerRequest,
    UserMcpServerResponse,
)
from chat.application.tools.core import Tool, ToolRegistry
from chat.application.tools.core.mcp import McpToolCatalog
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.entities.mcp_tool_server_config import McpToolStatus, UserMcpServerConfig
from chat.domain.entities.tool_config import UserToolConfig
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import McpServerConfigRepository, ToolConfigRepository
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
    tools = await tool_registry.system_tools()

    return R.success(data=ListUserToolsResponse(
        tools=[
            _build_tool_response(tool, configs.get(name))
            for name, tool in sorted(tools.items(), key=lambda item: item[0])
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
    tool = (await tool_registry.system_tools()).get(tool_name)
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
    tool = (await tool_registry.system_tools()).get(req.tool_name)
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
    tool = (await tool_registry.system_tools()).get(req.tool_name)
    if tool is None:
        raise ServiceException(ChatErrorCode.TOOL_NOT_FOUND)
    await tool_config_repo.delete_tool_config(user_id, req.tool_name)
    return R.success()


def _validate_mcp_headers(headers: dict[str, str], field_name: str) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        name = key.strip()
        if not name:
            raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, f"{field_name} contains a blank header name")
        if not value.strip():
            raise ServiceException(
                ChatErrorCode.TOOL_CONFIG_INVALID,
                f"{field_name}.{key} must be a non-blank string",
            )
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, f"{field_name}.{key} contains an unsafe newline")
        normalized[name] = value
    return normalized


@router.get(
    "/listUserMcpServers",
    response_model=R[ListUserMcpServersResponse],
    summary="查询用户 MCP 列表",
    description="""
- 用途：查询当前用户维护的 MCP server 配置列表，用于 MCP 工具管理页展示。
- 请求：无业务请求参数，用户身份来自请求上下文。
- 约束：当前用户必须已登录。
- 处理：读取当前用户保存的 MCP server 配置、启用工具名和密钥指纹；不返回 secret header 明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN。
- 响应：返回当前用户 MCP server 配置列表。
""",
)
@inject
async def list_user_mcp_servers(
    user_id: str = Depends(require_login),
    mcp_server_config_repo: McpServerConfigRepository = Depends(Provide[Container.mcp_server_config_repo]),
):
    configs = await mcp_server_config_repo.list_server_configs(user_id)
    servers: list[UserMcpServerResponse] = []
    for config in configs:
        servers.append(UserMcpServerResponse(
            server_id=config.server_id,
            display_name=config.display_name,
            url=config.url,
            enabled=config.enabled,
            headers=dict(config.headers or {}),
            secret_header_fingerprints=dict(config.secret_header_fingerprints or {}),
            enabled_tool_names=list(config.enabled_tool_names or []),
        ))
    return R.success(data=ListUserMcpServersResponse(
        servers=servers,
    ))


@router.get(
    "/getUserMcpServer",
    response_model=R[UserMcpServerResponse],
    summary="查询用户 MCP 详情",
    description="""
- 用途：查询当前用户某个 MCP server 配置详情，用于配置页编辑和诊断。
- 请求：server_id 指定目标 MCP server。
- 约束：当前用户必须已登录；目标 MCP server 配置必须属于当前用户。
- 处理：读取目标 MCP server 配置、启用工具名和密钥指纹；不返回 secret header 明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；MCP server 配置不存在 -> ChatErrorCode.TOOL_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回目标 MCP server 配置详情。
""",
)
@inject
async def get_user_mcp_server(
    server_id: str = Query(..., description="MCP server id"),
    user_id: str = Depends(require_login),
    mcp_server_config_repo: McpServerConfigRepository = Depends(Provide[Container.mcp_server_config_repo]),
):
    entity = await mcp_server_config_repo.get_server_config(user_id, req.server_id)
    if entity is None:
        raise ServiceException(ChatErrorCode.TOOL_NOT_FOUND)
    return R.success(data=UserMcpServerResponse(
        server_id=entity.server_id,
        display_name=entity.display_name,
        url=entity.url,
        enabled=entity.enabled,
        headers=dict(entity.headers or {}),
        secret_header_fingerprints=dict(entity.secret_header_fingerprints or {}),
        enabled_tool_names=list(entity.enabled_tool_names or []),
    ))


@router.post(
    "/previewUserMcpServer",
    response_model=R[PreviewUserMcpServerResponse],
    status_code=200,
    summary="预览用户 MCP",
    description="""
- 用途：在保存前探测一个 MCP server 暴露的工具列表，供用户选择启用哪些远端工具。
- 请求：url、headers、secret_headers 描述待探测 MCP server；enabled_tool_names 仅作为预览上下文保留。
- 约束：当前用户必须已登录；URL 和 header 配置必须满足本地安全校验。
- 处理：校验 URL 的 scheme、主机和 SSRF 风险，按用户配置的 header 调用 MCP list_tools，并返回工具快照；不保存配置，不返回 secret header 明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；MCP server 配置不合法 -> ChatErrorCode.TOOL_CONFIG_INVALID；MCP server 不可达 -> ChatErrorCode.MCP_TOOL_SERVER_UNREACHABLE；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回探测状态、错误文本和可用工具快照。
""",
)
@inject
async def preview_user_mcp_server(
    req: PreviewUserMcpServerRequest,
    user_id: str = Depends(require_login),
    mcp_tool_catalog: McpToolCatalog = Depends(Provide[Container.mcp_tool_catalog]),
):
    headers = _validate_mcp_headers(req.headers, "headers")
    secret_headers = _validate_mcp_headers(req.secret_headers, "secret_headers")

    tools = await mcp_tool_catalog.get_user_mcp_tools_info(
        UserMcpServerConfig(
            user_id=user_id,
            server_id=uuid.uuid4().hex,
            display_name=req.display_name,
            url=req.url,
            enabled=req.enabled,
            headers=headers,
            secret_headers=secret_headers,
            enabled_tool_names=list(req.enabled_tool_names or []),
        )
    )
    return R.success(data=PreviewUserMcpServerResponse(
        status=McpToolStatus.AVAILABLE,
        error="",
        tools=[
            McpToolSnapshotResponse(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema or {}),
                status=tool.status,
            )
            for tool in tools
        ],
    ))


@router.post(
    "/upsertUserMcpServer",
    response_model=R[UserMcpServerResponse],
    status_code=200,
    summary="保存用户 MCP",
    description="""
- 用途：创建或更新当前用户的 MCP server 配置。
- 请求：server_id 为空时新建配置，非空时更新已有配置；url 是 Streamable HTTP MCP 地址；headers 是普通请求头；secret_headers 是密钥请求头；enabled_tool_names 是用户希望启用的远端工具名。
- 约束：当前用户必须已登录；URL 和 header 配置必须满足本地安全校验；新建时不能超过每用户 MCP server 数量上限。
- 处理：保存配置和 secret header 指纹；不把用户 MCP 塞进旧 Tool 配置表，不返回 secret header 明文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；MCP server 配置不合法或数量超限 -> ChatErrorCode.TOOL_CONFIG_INVALID；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回保存后的 MCP server 配置和密钥指纹。
""",
)
@inject
async def upsert_user_mcp_server(
    req: UpsertUserMcpServerRequest,
    user_id: str = Depends(require_login),
    mcp_server_config_repo: McpServerConfigRepository = Depends(Provide[Container.mcp_server_config_repo]),
):
    server_id = uuid.uuid4().hex
    headers = _validate_mcp_headers(req.headers, "headers")

    existing = await mcp_server_config_repo.get_server_config(user_id, server_id) if req.server_id else None
    if req.server_id and existing is None:
        raise ServiceException(ChatErrorCode.TOOL_NOT_FOUND)
    if existing is None:
        server_count = len(await mcp_server_config_repo.list_server_configs(user_id))
        if server_count >= settings.MCP_MAX_USER_SERVERS:
            raise ServiceException(ChatErrorCode.TOOL_CONFIG_INVALID, "too many MCP server configs")

    if req.secret_headers is None:
        secret_headers = dict(existing.secret_headers if existing is not None else {})
        secret_header_fingerprints = dict(existing.secret_header_fingerprints if existing is not None else {})
    else:
        secret_headers = _validate_mcp_headers(req.secret_headers, "secret_headers")
        secret_header_fingerprints = {
            key: "*" * len(value) if len(value) <= 8 else f"{value[:4]}***{value[-4:]}"
            for key, value in secret_headers.items()
        }

    entity = await mcp_server_config_repo.upsert_server_config(
        user_id=user_id,
        server_id=server_id,
        display_name=req.display_name,
        url=req.url,
        enabled=req.enabled,
        headers=headers,
        secret_headers=secret_headers,
        secret_header_fingerprints=secret_header_fingerprints,
        enabled_tool_names=list(req.enabled_tool_names or []),
    )
    return R.success(data=UserMcpServerResponse(
        server_id=entity.server_id,
        display_name=entity.display_name,
        url=entity.url,
        enabled=entity.enabled,
        headers=dict(entity.headers or {}),
        secret_header_fingerprints=dict(entity.secret_header_fingerprints or {}),
        enabled_tool_names=list(entity.enabled_tool_names or []),
    ))


@router.post(
    "/deleteUserMcpServer",
    response_model=R,
    status_code=200,
    summary="删除用户 MCP",
    description="""
- 用途：删除当前用户保存的 MCP server 配置。
- 请求：server_id 指定目标 MCP server。
- 约束：当前用户必须已登录；server_id 必须满足本地格式校验。
- 处理：删除当前用户保存的目标 MCP server 配置；不影响 static/system tools，也不调用远端 MCP server。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；MCP server 配置不合法 -> ChatErrorCode.TOOL_CONFIG_INVALID；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_user_mcp_server(
    req: DeleteUserMcpServerRequest,
    user_id: str = Depends(require_login),
    mcp_server_config_repo: McpServerConfigRepository = Depends(Provide[Container.mcp_server_config_repo]),
):
    await mcp_server_config_repo.delete_server_config(user_id, req.server_id)
    return R.success()
