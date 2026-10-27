from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from common.logger import error

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

ToolSource = Literal["system", "sandbox"]

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

_SCHOLAR_OPENALEX_API_KEY_CONFIG = ToolConfigSpec(
    schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "OpenAlex API Key",
                "description": "可选；未配置时 Scholar 仍可使用本地论文数据。",
                "writeOnly": True,
            },
        },
        "additionalProperties": False,
    },
    secret_keys=("api_key",),
)

_WEB_SEARCH_POLICY = ToolPolicy(
    expose_by_default=True,
    risk_level=ToolRiskLevel.LOW,
    timeout_seconds=30.0,
    persist_output=True,
    max_output_chars=None,
)

_SCHOLAR_POLICY = ToolPolicy(
    expose_by_default=True,
    risk_level=ToolRiskLevel.LOW,
    timeout_seconds=30.0,
    persist_output=True,
    max_output_chars=None,
)

_SYSTEM_TOOL_CONFIGS: list[dict[str, Any]] = [{
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
        "tool_name": "create_note",
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "ui_spec": ToolUISpec(display_name="创建笔记", description="为当前用户创建一个新的空笔记。"),
        "failure_reason": "Note Create Failed",
    }, {
        "tool_name": "read_current_note_for_edit",
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=10.0,
            persist_output=True,
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "ui_spec": ToolUISpec(display_name="读取笔记编辑上下文", description="读取当前用户可见笔记的结构化编辑上下文。"),
        "failure_reason": "Note Read For Edit Failed",
    }, {
        "tool_name": "apply_current_note_edits",
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "ui_spec": ToolUISpec(display_name="应用笔记编辑", description="将结构化编辑建议应用到当前用户可编辑的笔记。"),
        "failure_reason": "Note Edit Apply Failed",
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
    },{
        "tool_name": "search_papers",
        "ui_spec": ToolUISpec(display_name="搜索论文"),
        "policy": _SCHOLAR_POLICY,
        "config_spec": _SCHOLAR_OPENALEX_API_KEY_CONFIG,
        "failure_reason": "Scholar Paper Search Failed",
    },{
        "tool_name": "lookup_papers",
        "ui_spec": ToolUISpec(display_name="查询论文"),
        "policy": _SCHOLAR_POLICY,
        "config_spec": _SCHOLAR_OPENALEX_API_KEY_CONFIG,
        "failure_reason": "Scholar Paper Lookup Failed",
    },{
        "tool_name": "discover_papers",
        "ui_spec": ToolUISpec(display_name="发现论文"),
        "policy": _SCHOLAR_POLICY,
        "failure_reason": "Scholar Paper Discovery Failed",
    }
]


_SANDBOX_TOOL_CONFIGS: list[dict[str, Any]] = [
    {
        "tool_name": "execute_command",
        "ui_spec": ToolUISpec(display_name="执行沙箱命令"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=620.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox Command Execution Failed",
    },
    {
        "tool_name": "start_command",
        "ui_spec": ToolUISpec(display_name="启动沙箱命令"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox Command Start Failed",
    },
    {
        "tool_name": "get_command_result",
        "ui_spec": ToolUISpec(display_name="读取沙箱命令结果"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=620.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox Command Result Read Failed",
    },
    {
        "tool_name": "kill_command",
        "ui_spec": ToolUISpec(display_name="终止沙箱命令"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox Command Termination Failed",
    },
    {
        "tool_name": "read_file",
        "ui_spec": ToolUISpec(display_name="读取沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Read Failed",
    },
    {
        "tool_name": "write_file",
        "ui_spec": ToolUISpec(display_name="写入沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Write Failed",
    },
    {
        "tool_name": "replace_in_file",
        "ui_spec": ToolUISpec(display_name="替换沙箱文件内容"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Replace Failed",
    },
    {
        "tool_name": "search_in_file",
        "ui_spec": ToolUISpec(display_name="搜索沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Search Failed",
    },
    {
        "tool_name": "find_files",
        "ui_spec": ToolUISpec(display_name="查找沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Find Failed",
    },
    {
        "tool_name": "grep_files",
        "ui_spec": ToolUISpec(display_name="检索沙箱文件内容"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Grep Failed",
    },
    {
        "tool_name": "glob_files",
        "ui_spec": ToolUISpec(display_name="Glob 沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Glob Failed",
    },
    {
        "tool_name": "list_files",
        "ui_spec": ToolUISpec(display_name="列出沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File List Failed",
    },
    {
        "tool_name": "upload_file",
        "ui_spec": ToolUISpec(display_name="导入附件到沙箱"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=150.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id", "temporary_attachment_refs"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox Attachment Import Failed",
    },
    {
        "tool_name": "download_file",
        "ui_spec": ToolUISpec(display_name="导出沙箱文件"),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=150.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox File Export Failed",
    },
    {
        "tool_name": "upload_file_to_cloud_drive",
        "ui_spec": ToolUISpec(
            display_name="上传沙箱文件到云盘",
            description="将沙箱中的文档或 HTML 文件上传为新的云盘资源。",
        ),
        "policy": ToolPolicy(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=150.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
            max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
        ),
        "failure_reason": "Sandbox Cloud Resource Upload Failed",
    },
]


class SystemMcpToolCatalog:
    def __init__(
        self,
        *,
        mcp_service_client: McpServiceClient,
        sandbox_mcp_service_client: McpServiceClient,
    ) -> None:
        self._mcp_service_client = mcp_service_client
        self._sandbox_mcp_service_client = sandbox_mcp_service_client
        self._tool_cache: dict[ToolSource, tuple[float, list[McpToolDescriptor]]] = {}

    async def _load_tools(
        self,
        source: ToolSource,
        client: McpServiceClient,
    ) -> list[McpToolDescriptor]:
        ttl = max(0.0, settings.MCP_SYSTEM_LIST_TOOLS_CACHE_TTL_SECONDS)
        now = time.monotonic()
        cached = self._tool_cache.get(source)

        # 缓存未过期时直接返回该来源的工具描述。
        if cached is not None:
            cached_at, descriptors = cached
            if cached_at + ttl > now:
                return list(descriptors)

        try:
            descriptors = await client.list_tools()
        except Exception as exc:
            error("load internal mcp tools failed.", source=source, exc=exc)
            # 当前请求失败时继续使用该来源上一次成功的结果。
            return list(cached[1]) if cached is not None else []

        # 只有成功拉取后才刷新时间，避免网络请求耗时消耗缓存 TTL。
        self._tool_cache[source] = (time.monotonic(), list(descriptors))
        return list(descriptors)

    async def load_system_tools(self) -> dict[str, McpRemoteTool]:
        # 在服务发现之前排除沙箱，避免以搜索为主的实验等待沙箱连接。
        if settings.MCP_SANDBOX_ENABLED:
            system_descriptors, sandbox_descriptors = await asyncio.gather(
                self._load_tools("system", self._mcp_service_client),
                self._load_tools("sandbox", self._sandbox_mcp_service_client),
            )
        else:
            system_descriptors = await self._load_tools("system", self._mcp_service_client)
        tools: dict[str, McpRemoteTool] = {}
        tools.update(self._build_remote_tools(
            system_descriptors,
            self._mcp_service_client,
            _SYSTEM_TOOL_CONFIGS,
            prefix="system_",
        ))
        if settings.MCP_SANDBOX_ENABLED:
            tools.update(self._build_remote_tools(
                sandbox_descriptors,
                self._sandbox_mcp_service_client,
                _SANDBOX_TOOL_CONFIGS,
                prefix="sandbox_",
            ))
        return tools

    @staticmethod
    def _build_remote_tools(
        descriptors: list[McpToolDescriptor],
        client: McpServiceClient,
        configs: list[dict[str, Any]],
        *,
        prefix: str,
    ) -> dict[str, McpRemoteTool]:
        # 每个客户端只向对应 MCP 服务发现工具；注册到 Chat 时统一加来源前缀。
        config_by_remote_name = {item["tool_name"]: item for item in configs}
        tools: dict[str, McpRemoteTool] = {}
        for descriptor in descriptors:
            overlay = config_by_remote_name.get(descriptor.name)
            if overlay is None:
                continue
            try:
                parameters_schema = ToolParametersSchema(descriptor.input_schema)
            except (TypeError, ValueError):
                continue
            registered_name = f"{prefix}{overlay['tool_name']}"
            tools[registered_name] = McpRemoteTool(
                mcp_client=client,
                server=None,
                remote_name=descriptor.name,
                definition=ToolDefinition(
                    llm_spec=ToolLLMSpec(
                        name=registered_name,
                        description=(descriptor.description or "").strip(),
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
