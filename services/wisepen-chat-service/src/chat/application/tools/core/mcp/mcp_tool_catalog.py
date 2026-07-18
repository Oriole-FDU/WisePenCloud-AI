from __future__ import annotations

import re

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.mcp.mcp_client import McpClient, McpServerConnection
from chat.application.tools.core.mcp.remote_tool import McpRemoteTool
from chat.core.config.app_settings import settings
from chat.domain.entities.mcp_tool_server_config import McpToolSnapshot, McpToolStatus, UserMcpServerConfig
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import McpServerConfigRepository
from chat.domain.repositories.mcp_tool_discovery_cache_repo import McpToolDiscoveryCacheRepository
from common.core.exceptions import ServiceException


class McpToolCatalog:
    def __init__(
        self,
        *,
        mcp_client: McpClient,
        mcp_tool_discovery_cache_repo: McpToolDiscoveryCacheRepository,
        mcp_server_config_repo: McpServerConfigRepository,
    ) -> None:
        self._mcp_client = mcp_client
        self._mcp_tool_discovery_cache_repo = mcp_tool_discovery_cache_repo
        self._mcp_server_config_repo = mcp_server_config_repo

    async def load_user_mcp_tools(self, user_id: str) -> dict[str, McpRemoteTool]:
        tools: dict[str, McpRemoteTool] = {}
        # 加载用户配置
        configs = await self._mcp_server_config_repo.list_server_configs(user_id)
        for config in configs[:settings.MCP_MAX_USER_SERVERS]:
            if not config.enabled:
                continue # 配置未启用，跳过
            try:
                server = McpServerConnection(config)
                descriptors = await self._mcp_tool_discovery_cache_repo.get_user_tools(
                    user_id=user_id, server_id=config.server_id, config_updated_at=config.updated_at
                ) # 优先从缓存中加载工具配置

                if descriptors is None: # 缓存未命中
                    descriptors = await self._mcp_client.list_tools(server) # 从 MCP 服务器拉取
                    await self._mcp_tool_discovery_cache_repo.set_user_tools(
                        user_id=user_id, server_id=config.server_id, config_updated_at=config.updated_at,
                        tools=descriptors,
                        ttl_seconds=int(settings.MCP_USER_LIST_TOOLS_CACHE_TTL_SECONDS),
                    ) # 存入缓存
            except Exception:
                continue

            enabled_remote_names = set(config.enabled_tool_names) # 启用的工具名

            for descriptor in descriptors[:settings.MCP_MAX_TOOLS_PER_SERVER]:
                if descriptor.name not in enabled_remote_names: continue # 跳过未启用的 Tool
                schema = descriptor.input_schema or {}
                try:
                    parameters_schema = ToolParametersSchema(schema)
                except (TypeError, ValueError): # schema 不符合 ToolParametersSchema 要求，跳过
                    continue

                tool_server_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", (config.server_id or "").strip()).strip("_").lower() or "tool"
                tool_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", (descriptor.name or "").strip()).strip("_").lower() or "tool"
                mcp_tool_name = f"mcp__{tool_server_id}__{tool_name}"

                tool_description = (descriptor.description or "").strip()

                # TODO: 这里使用默认配置，后续允许根据Tool指定配置
                policy = ToolPolicy(
                    expose_by_default=True,
                    risk_level=ToolRiskLevel.LOW,
                    timeout_seconds=settings.MCP_DEFAULT_TIMEOUT_SECONDS,
                    persist_output=True,
                    allow_parallel=False,
                    max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
                )

                # 构建 McpRemoteTool
                tool = McpRemoteTool(
                    mcp_client=self._mcp_client, server=server, remote_name=descriptor.name,
                    definition=ToolDefinition(
                        llm_spec=ToolLLMSpec(
                            name=mcp_tool_name,
                            description=tool_description,
                            parameters_schema=parameters_schema,
                        ),
                        policy=policy,
                        preflight_hooks=(),
                    ),
                    failure_reason="MCP Tool Execution Failed",
                )
                tools[tool.definition.llm_spec.name] = tool # McpRemoteTool 加入 Tool 列表
        return tools

    async def get_user_mcp_tools_info(self, config: UserMcpServerConfig) -> list[McpToolSnapshot]:
        try:
            server = McpServerConnection(config)
            descriptors = await self._mcp_client.list_tools(server)
        except Exception as e:
            raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_UNREACHABLE, custom_msg=str(e))

        snapshots: list[McpToolSnapshot] = []
        for descriptor in descriptors[:settings.MCP_MAX_TOOLS_PER_SERVER]:
            description = (descriptor.description or "").strip()
            schema = descriptor.input_schema or {}
            try:
                ToolParametersSchema(schema)
                status = McpToolStatus.AVAILABLE
            except (TypeError, ValueError):
                status = McpToolStatus.INVALID_SCHEMA

            snapshots.append(
                McpToolSnapshot(
                    name=descriptor.name,
                    description=description,
                    input_schema=schema,
                    status=status,
                )
            )
        return snapshots
