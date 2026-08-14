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
    ToolSelectionMode,
    ToolUISpec,
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

_SYSTEM_TOOL_CONFIGS: List[dict[str, Any]] = [{
        "tool_name": "create_skill_info",
        "ui_spec": ToolUISpec(display_name="创建 Skill 信息", description="创建新的 Skill 草稿信息。"),
        "policy": ToolPolicy(
            expose_by_default=False,
            selection_mode=ToolSelectionMode.CONTEXTUAL,
            risk_level=ToolRiskLevel.HIGH,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Info Create Failed",
    }, {
        "tool_name": "get_skill_info",
        "ui_spec": ToolUISpec(display_name="读取 Skill 信息", description="读取 Skill 草稿信息。"),
        "policy": ToolPolicy(
            expose_by_default=False,
            selection_mode=ToolSelectionMode.CONTEXTUAL,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Info Load Failed",
    }, {
        "tool_name": "update_skill_info",
        "ui_spec": ToolUISpec(display_name="更新 Skill 信息", description="更新 Skill 草稿的名称和描述。"),
        "policy": ToolPolicy(
            expose_by_default=False,
            selection_mode=ToolSelectionMode.CONTEXTUAL,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Info Update Failed",
    }, {
        "tool_name": "upload_skill_draft_asset",
        "ui_spec": ToolUISpec(display_name="上传 Skill 资源", description="向 Skill 草稿上传文本资源。"),
        "policy": ToolPolicy(
            expose_by_default=False,
            selection_mode=ToolSelectionMode.CONTEXTUAL,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:skill-creator",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Skill Draft Asset Upload Failed",
    },
    # AI Note
    {
        "tool_name": "read_current_note_for_edit",
        "policy": ToolPolicy(
            expose_by_default=False,
            selection_mode=ToolSelectionMode.CONTEXTUAL,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=10.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:current-note-editor",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "ui_spec": ToolUISpec(display_name="读取当前笔记", description="读取当前已经打开的笔记的编辑上下文。"),
        "failure_reason": "Current Note Read Failed",
    }, {
        "tool_name": "apply_current_note_edits",
        "policy": ToolPolicy(
            expose_by_default=False,
            selection_mode=ToolSelectionMode.CONTEXTUAL,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("allowed_skill_ids",),
            required_allowed_builtin_skill_ids=("builtin:current-note-editor",),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "ui_spec": ToolUISpec(display_name="应用当前笔记编辑", description="将结构化编辑建议应用到当前已经打开的笔记。"),
        "failure_reason": "Current Note Edit Apply Failed",
    },
    # User Resource Tools
    {
        "tool_name": "search_user_resources",
        "ui_spec": ToolUISpec(display_name="搜索用户资源", description="搜索当前用户可见的笔记和文档资源。"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=15.0,
            persist_output=True,
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "User Resource Search Failed",
    }, {
        "tool_name": "read_note_resource_text",
        "ui_spec": ToolUISpec(display_name="快速读取笔记文本", description="读取当前用户可见（无需已经打开）笔记的纯文本内容，用于快速了解，不能用于编辑。"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=20.0,
            persist_output=True,
            max_output_chars=None,
        ),
        "failure_reason": "Note Resource Text Read Failed",
    }, {
        "tool_name": "read_document_resource_text",
        "ui_spec": ToolUISpec(display_name="读取文档资源文本", description="读取当前用户可见（无需已经打开）文档资源的转换或抽取文本。"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=20.0,
            persist_output=True,
            max_output_chars=None,
        ),
        "failure_reason": "Document Resource Text Read Failed",
    },
    # Web Search Tools
    {
        "tool_name": "default_web_search",
        "ui_spec": ToolUISpec(display_name="默认 Web 搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "failure_reason": "Default Web Search Failed",
    },{
        "tool_name": "exa_search",
        "ui_spec": ToolUISpec(display_name="Exa 搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Exa Search Failed",
    },{
        "tool_name": "tavily_search",
        "ui_spec": ToolUISpec(display_name="Tavily 搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Tavily Search Failed",
    },{
        "tool_name": "anysearch_search",
        "ui_spec": ToolUISpec(display_name="AnySearch 搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "AnySearch Search Failed",
    },{
        "tool_name": "baidu_qianfan_search",
        "ui_spec": ToolUISpec(display_name="百度千帆搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Baidu Qianfan Search Failed",
    },{
        "tool_name": "tinyfish_search",
        "ui_spec": ToolUISpec(display_name="TinyFish 搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "TinyFish Search Failed",
    },{
        "tool_name": "firecrawl_search",
        "ui_spec": ToolUISpec(display_name="Firecrawl 搜索"),
        "policy": _WEB_SEARCH_POLICY,
        "config_spec": _WEB_SEARCH_API_KEY_CONFIG,
        "failure_reason": "Firecrawl Search Failed",
    }
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
                    ui_spec=overlay.get("ui_spec"),
                    config_spec=overlay.get("config_spec"),
                    preflight_hooks=(),
                ),
                failure_reason=overlay["failure_reason"],
            )
        return tools
