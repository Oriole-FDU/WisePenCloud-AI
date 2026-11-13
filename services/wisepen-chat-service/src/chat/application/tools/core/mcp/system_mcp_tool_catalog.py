from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from chat.application.tools.core import (
    OutputCacheSpec,
    OutputCacheStrategy,
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
from common.mcp import ToolMeta

ToolSource = Literal["system", "sandbox"]

T = TypeVar("T")


def _to_tool_policy(meta: ToolMeta) -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=meta.policy.expose_by_default,
        selection_mode=ToolSelectionMode(meta.policy.selection_mode.value),
        risk_level=ToolRiskLevel(meta.policy.risk_level.value),
        timeout_seconds=meta.policy.timeout_seconds,
        persist_output=meta.policy.persist_output,
        required_context_keys=meta.policy.required_context_keys,
        required_allowed_builtin_skill_ids=meta.policy.required_allowed_builtin_skill_ids,
        max_output_chars=(
            settings.tools.result_max_chars
            if meta.output_cache_spec.strategy.value == "none"
            else None
        ),
    )


def _to_ui_spec(meta: ToolMeta) -> ToolUISpec | None:
    if meta.ui_spec is None:
        return None
    return ToolUISpec(
        display_name=meta.ui_spec.display_name,
        description=meta.ui_spec.description,
    )


def _to_config_spec(meta: ToolMeta) -> ToolConfigSpec | None:
    if meta.config_spec is None:
        return None
    return ToolConfigSpec(
        schema=meta.config_spec.config_schema,
        required_keys=meta.config_spec.required_keys,
        secret_keys=meta.config_spec.secret_keys,
    )


def _to_output_cache_spec(meta: ToolMeta) -> OutputCacheSpec:
    return OutputCacheSpec(
        strategy=OutputCacheStrategy(meta.output_cache_spec.strategy.value),
        cache_paths=meta.output_cache_spec.cache_paths,
    )


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
        self._meta_cache: dict[ToolSource, tuple[float, list[ToolMeta]]] = {}

    async def _load(
        self,
        source: ToolSource,
        cache: dict[ToolSource, tuple[float, list[T]]],
        fetch: Callable[[], Awaitable[list[T]]],
        *,
        label: str,
    ) -> list[T]:
        ttl = max(0.0, settings.mcp.system_list_tools_cache_ttl_seconds)
        now = time.monotonic()
        cached = cache.get(source)

        # 缓存未过期时直接返回
        if cached is not None:
            cached_at, values = cached
            if cached_at + ttl > now:
                return list(values)

        try:
            values = await fetch()
        except Exception as exc:
            error(f"load internal mcp {label} failed.", source=source, exc=exc)
            # 当前请求失败时继续使用该来源上一次成功的结果
            return list(cached[1]) if cached is not None else []

        # 只有成功拉取后才刷新时间，避免网络请求耗时消耗缓存 TTL
        cache[source] = (time.monotonic(), list(values))
        return list(values)

    async def load_system_tools(self) -> dict[str, McpRemoteTool]:
        system_descriptors, sandbox_descriptors, system_metas, sandbox_metas = await asyncio.gather(
            self._load("system", self._tool_cache, self._mcp_service_client.list_tools, label="tools"),
            self._load("sandbox", self._tool_cache, self._sandbox_mcp_service_client.list_tools, label="tools"),
            self._load("system", self._meta_cache, self._mcp_service_client.list_tool_metas, label="tool metas"),
            self._load("sandbox", self._meta_cache, self._sandbox_mcp_service_client.list_tool_metas, label="tool metas"),
        )
        tools: dict[str, McpRemoteTool] = {}
        tools.update(self._build_remote_tools(
            system_descriptors,
            self._mcp_service_client,
            system_metas,
            prefix="system_",
        ))
        tools.update(self._build_remote_tools(
            sandbox_descriptors,
            self._sandbox_mcp_service_client,
            sandbox_metas,
            prefix="sandbox_",
        ))
        return tools

    def _build_remote_tools(
        self,
        descriptors: list[McpToolDescriptor],
        client: McpServiceClient,
        metas: list[ToolMeta],
        *,
        prefix: str,
    ) -> dict[str, McpRemoteTool]:
        meta_by_name = {meta.tool_name: meta for meta in metas}
        tools: dict[str, McpRemoteTool] = {}
        for descriptor in descriptors:
            meta = meta_by_name.get(descriptor.name)
            if meta is None:
                continue
            try:
                parameters_schema = ToolParametersSchema(descriptor.input_schema)
            except (TypeError, ValueError):
                continue
            registered_name = f"{prefix}{meta.tool_name}"
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
                    policy=_to_tool_policy(meta),
                    ui_spec=_to_ui_spec(meta),
                    config_spec=_to_config_spec(meta),
                    output_cache_spec=_to_output_cache_spec(meta),
                ),
                failure_reason=meta.failure_reason,
            )
        return tools
