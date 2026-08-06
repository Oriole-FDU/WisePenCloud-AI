from __future__ import annotations

import time
from typing import Any, List

from chat.application.tools.core import (
    ToolConfigSpec,
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.mcp.remote_tool import McpRemoteTool
from chat.core.config.app_settings import settings
from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor
from chat.service_client import McpServiceClient
from common.logger import error

_WEB_SEARCH_API_KEY_CONFIG = ToolConfigSpec(
    schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "API Key",
                "description": "API key for the configured search provider.",
                "writeOnly": True,
            },
        },
        "additionalProperties": False,
    },
    required_keys=("api_key",),
    secret_keys=("api_key",),
)

_WEB_SEARCH_POLICY = ToolPolicy(
    expose_by_default=True,
    risk_level=ToolRiskLevel.LOW,
    timeout_seconds=100.0,
    persist_output=True,
    max_output_chars=None,
)

_RAG_NAVIGATION_POLICY = ToolPolicy(
    expose_by_default=True,
    risk_level=ToolRiskLevel.LOW,
    timeout_seconds=300.0,
    persist_output=True,
    required_context_keys=("session_id",),
)

_SYSTEM_TOOL_CONFIGS: List[dict[str, Any]] = [
    # 1. 技能创建与管理工具
    {
        "tool_name": "create_skill_info",
        "policy": ToolPolicy(
            expose_by_default=False,
            risk_level=ToolRiskLevel.HIGH,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Info Create Failed",
    },
    {
        "tool_name": "get_skill_info",
        "policy": ToolPolicy(
            expose_by_default=False,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Info Load Failed",
    },
    {
        "tool_name": "update_skill_info",
        "policy": ToolPolicy(
            expose_by_default=False,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Info Update Failed",
    },
    {
        "tool_name": "upload_skill_draft_asset",
        "policy": ToolPolicy(
            expose_by_default=False,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Draft Asset Upload Failed",
    },

    # 2. 知识库/ RAG 导航工具
    {
        "tool_name": "knowledge_navigate_locate",
        "policy": _RAG_NAVIGATION_POLICY,
        "failure_reason": "Knowledge Navigation Locate Failed",
    },
    {
        "tool_name": "knowledge_navigate_cypher",
        "policy": _RAG_NAVIGATION_POLICY,
        "failure_reason": "Knowledge Navigation Cypher Failed",
    },
    {
        "tool_name": "knowledge_navigate_sections",
        "policy": _RAG_NAVIGATION_POLICY,
        "failure_reason": "Knowledge Navigation Sections Failed",
    },
    {
        "tool_name": "rag_get_document_structure",
        "policy": _RAG_NAVIGATION_POLICY,
        "failure_reason": "Document Structure Load Failed",
    },
    {
        "tool_name": "rag_get_page_content",
        "policy": _RAG_NAVIGATION_POLICY,
        "failure_reason": "Page Content Read Failed",
    },
    {
        "tool_name": "rag_get_section_content",
        "policy": _RAG_NAVIGATION_POLICY,
        "failure_reason": "Section Content Read Failed",
    },

    # 3. Web Search
    {
        "tool_name": "platform_search",
        "policy": _WEB_SEARCH_POLICY,
        "failure_reason": "Platform Search Failed",
    },
    {
        "tool_name": "exa_search",
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Exa Search Failed",
    },
    {
        "tool_name": "tavily_search",
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Tavily Search Failed",
    },
    {
        "tool_name": "anysearch_search",
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "AnySearch Search Failed",
    },
    {
        "tool_name": "baidu_qianfan_search",
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Baidu Qianfan Search Failed",
    },
    {
        "tool_name": "tinyfish_search",
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "TinyFish Search Failed",
    },
    {
        "tool_name": "firecrawl_search",
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Firecrawl Search Failed",
    },
]


class SystemMcpToolCatalog:
    def __init__(self, *, mcp_service_client: McpServiceClient) -> None:
        self._mcp_service_client = mcp_service_client
        self._mcp_tools_cache_update_time: float | None = None
        self._mcp_tools_cache: list[McpToolDescriptor] | None = None

    async def load_system_tools(self) -> dict[str, McpRemoteTool]:
        ttl = max(0.0, settings.MCP_SYSTEM_LIST_TOOLS_CACHE_TTL_SECONDS)
        now = time.monotonic()
        # 缓存尚未过期
        if self._mcp_tools_cache is not None and self._mcp_tools_cache_update_time + ttl > now:
            descriptors = list(self._mcp_tools_cache)
        else:
            # 重新拉取缓存
            try:
                descriptors = await self._mcp_service_client.list_tools()
            except Exception as e:
                error("load system mcp tools failed.", exc=e)
                return {}
            self._mcp_tools_cache_update_time = now

        tools: dict[str, McpRemoteTool] = {}
        for descriptor in descriptors:
            tool_configs = {item["tool_name"] : item for item in _SYSTEM_TOOL_CONFIGS}
            overlay = tool_configs.get(descriptor.name)
            if overlay is None: # 仅加载显式声明的 Tool
                continue
            try:
                parameters_schema = ToolParametersSchema(descriptor.input_schema)
            except (TypeError, ValueError):
                continue
            description = (descriptor.description or "").strip()

            tools[overlay["tool_name"]] = McpRemoteTool(
                mcp_client=self._mcp_service_client,
                server=None, # 内部 MCP 服务无需 server
                remote_name=descriptor.name,
                definition=ToolDefinition(
                    llm_spec=ToolLLMSpec(
                        name=overlay["tool_name"],
                        description=description,
                        parameters_schema=parameters_schema,
                    ),
                    policy=overlay["policy"],
                    config_spec=overlay.get("config_spec"),
                    preflight_hooks=(),
                ),
                failure_reason=overlay["failure_reason"],
            )
        return tools
