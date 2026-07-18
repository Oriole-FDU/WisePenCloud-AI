from __future__ import annotations

import time
from typing import Any, List

from chat.application.tools.core import (
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

_SKILL_TOOL_CONFIGS: List[dict[str, Any]] = [{
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
    }, {
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
    }, {
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
    }
]

_SANDBOX_TOOL_CONFIGS: List[dict[str, Any]] = [{
    "tool_name": "read_file",
    "policy": ToolPolicy(
        expose_by_default=True,
        risk_level=ToolRiskLevel.LOW,
        timeout_seconds=30.0,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    ),
    "failure_reason": "Read File Failed",
}, {
    "tool_name": "write_file",
    "policy": ToolPolicy(
        expose_by_default=True,
        risk_level=ToolRiskLevel.LOW,
        timeout_seconds=30.0,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    ),
    "failure_reason": "Write File Failed",
}, {
    "tool_name": "list_directory",
    "policy": ToolPolicy(
        expose_by_default=True,
        risk_level=ToolRiskLevel.LOW,
        timeout_seconds=30.0,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    ),
    "failure_reason": "List Directory Failed",
}, {
    "tool_name": "grep_files",
    "policy": ToolPolicy(
        expose_by_default=True,
        risk_level=ToolRiskLevel.LOW,
        timeout_seconds=30.0,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    ),
    "failure_reason": "Grep Files Failed",
}, {
    "tool_name": "edit_file",
    "policy": ToolPolicy(
        expose_by_default=True,
        risk_level=ToolRiskLevel.MEDIUM,
        timeout_seconds=30.0,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    ),
    "failure_reason": "Edit File Failed",
}, {
    "tool_name": "shell_exec",
    "policy": ToolPolicy(
        expose_by_default=True,
        risk_level=ToolRiskLevel.MEDIUM,
        timeout_seconds=35.0,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    ),
    "failure_reason": "Shell Exec Failed",
}]


class SystemMcpToolCatalog:
    def __init__(self, *, mcp_service_client: McpServiceClient,
                 sandbox_mcp_client: McpServiceClient | None = None) -> None:
        self._mcp_service_client = mcp_service_client
        self._sandbox_mcp_client = sandbox_mcp_client
        self._mcp_tools_cache_update_time: float | None = None
        self._mcp_tools_cache: list[McpToolDescriptor] | None = None

    async def load_system_tools(self) -> dict[str, McpRemoteTool]:
        ttl = max(0.0, settings.MCP_SYSTEM_LIST_TOOLS_CACHE_TTL_SECONDS)
        now = time.monotonic()

        tools: dict[str, McpRemoteTool] = {}

        # ---- Skill MCP tools ----
        if self._mcp_tools_cache is not None and self._mcp_tools_cache_update_time + ttl > now:
            descriptors = list(self._mcp_tools_cache)
        else:
            try:
                descriptors = await self._mcp_service_client.list_tools()
            except Exception as e:
                error("load system mcp tools failed.", exc=e)
                descriptors = []
            self._mcp_tools_cache_update_time = now
            self._mcp_tools_cache = list(descriptors) if descriptors else None

        _merge_tools(tools, descriptors, _SKILL_TOOL_CONFIGS, self._mcp_service_client)

        # ---- Sandbox MCP tools ----
        if self._sandbox_mcp_client:
            try:
                sandbox_descriptors = await self._sandbox_mcp_client.list_tools()
            except Exception as e:
                error("load sandbox mcp tools failed.", exc=e)
                sandbox_descriptors = []
            _merge_tools(tools, sandbox_descriptors, _SANDBOX_TOOL_CONFIGS, self._sandbox_mcp_client)

        return tools


def _merge_tools(
    tools: dict[str, McpRemoteTool],
    descriptors: list[McpToolDescriptor],
    configs: list[dict[str, Any]],
    mcp_client: McpServiceClient,
) -> None:
    tool_configs = {item["tool_name"]: item for item in configs}
    for descriptor in descriptors:
        overlay = tool_configs.get(descriptor.name)
        if overlay is None:
            continue
        try:
            parameters_schema = ToolParametersSchema(descriptor.input_schema)
        except (TypeError, ValueError):
            continue
        description = (descriptor.description or "").strip()
        tools[overlay["tool_name"]] = McpRemoteTool(
            mcp_client=mcp_client,
            server=None,
            remote_name=descriptor.name,
            definition=ToolDefinition(
                llm_spec=ToolLLMSpec(
                    name=overlay["tool_name"],
                    description=description,
                    parameters_schema=parameters_schema,
                ),
                policy=overlay["policy"],
                preflight_hooks=(),
            ),
            failure_reason=overlay["failure_reason"],
        )
