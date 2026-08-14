from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat.application.tools.core.definition import Tool, ToolSelectionMode
from chat.application.tools.core.llm.renderer import schema_renderer
from chat.application.tools.client_tools import ClientToolCapability, client_tool_from_capability
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import ToolConfigRepository
from common.core.exceptions import ServiceException

if TYPE_CHECKING:
    from chat.application.tools.core.mcp import McpToolCatalog, SystemMcpToolCatalog


class ToolScope:
    """一次请求内的工具可见性和可信上下文快照"""

    def __init__(
        self,
        *,
        tools: dict[str, Tool],
        context: dict[str, Any] | None,
        configs: dict[str, dict[str, Any]] | None = None,
        client_tool_capabilities: list[ClientToolCapability] | None = None,
    ) -> None:
        self._tools = dict(tools)
        self._context = dict(context or {})
        self._configs = { name: dict(config) for name, config in (configs or {}).items() if name in self._tools}
        self._schemas: list[dict[str, Any]] = [schema_renderer(tool.definition.llm_spec) for tool in self._tools.values()]
        self._client_tool_capabilities = [
            client_tool_capability
            for client_tool_capability in (client_tool_capabilities or [])
            if client_tool_capability.name in self._tools
        ]

    def schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def config_for(self, name: str) -> dict[str, Any] | None:
        config = self._configs.get(name)
        return dict(config) if config is not None else None

    @property
    def context(self) -> dict[str, Any]:
        return dict(self._context)

    def __len__(self) -> int:
        return len(self._tools)

    def to_suspension_data(self) -> dict[str, Any]:
        return {
            "tool_names": list(self._tools),
            "context": dict(self._context),
            "configs": {name: dict(config) for name, config in self._configs.items()},
            "client_tool_capabilities": list(self._client_tool_capabilities),
        }

class ToolRegistry:
    """全局工具注册表，负责派生请求级工具视图"""

    def __init__(
        self,
        tool_config_repo: ToolConfigRepository,
        mcp_tool_catalog: McpToolCatalog | None = None,
        system_mcp_tool_catalog: SystemMcpToolCatalog | None = None,
    ) -> None:
        self._tool_config_repo = tool_config_repo
        self._mcp_tool_catalog = mcp_tool_catalog
        self._system_mcp_tool_catalog = system_mcp_tool_catalog
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.llm_spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """返回全局已注册工具的 schema。

        该方法仅用于诊断和测试。运行期 LLM 调用必须使用 ToolScope.schemas()，
        确保已应用当前请求的上下文和工具选择过滤。
        """
        return [schema_renderer(tool.definition.llm_spec) for tool in self._tools.values()]

    async def system_tools(self) -> dict[str, Tool]:
        system_tools = dict(self._tools)
        if self._system_mcp_tool_catalog is None:
            return system_tools

        # 收集系统内部 MCP 工具
        system_mcp_tools = await self._system_mcp_tool_catalog.load_system_tools()
        for name, tool in system_mcp_tools.items():
            if name not in system_tools:
                system_tools[name] = tool
        return system_tools

    async def available_tools(self, user_id: str) -> dict[str, Tool]:
        tools: dict[str, Tool] = await self.system_tools()

        # 收集用户配置的 MCP 工具
        if self._mcp_tool_catalog is not None:
            for name, tool in (await self._mcp_tool_catalog.load_user_mcp_tools(user_id)).items():
                tools.setdefault(name, tool) # 用户 MCP 工具不覆盖已有工具

        return tools

    # 推导当前工具
    async def derive(
        self,
        *,
        tool_context: dict[str, Any] | None = None,
        expose_tool_name_set: set[str] | None = None,
        tool_selection_default_enabled: bool = True,
        tool_selection_overrides: dict[str, bool] | None = None,
        client_tool_capabilities: list[ClientToolCapability] | None = None,
        user_id: str,
    ) -> ToolScope:
        context = dict(tool_context or {})
        expose_tool_name_set = expose_tool_name_set or set()
        tool_selection_overrides = dict(tool_selection_overrides or {})
        client_tool_capabilities = list(client_tool_capabilities or [])

        tools: dict[str, Tool] = await self.available_tools(user_id)

        # 构建前端工具
        for client_tool_capability in client_tool_capabilities:
            if client_tool_capability.name in tools:
                raise ServiceException(
                    ChatErrorCode.TOOL_CONFIG_INVALID, custom_msg=f"客户端工具名称冲突: {client_tool_capability.name}",
                )
            tools[client_tool_capability.name] = client_tool_from_capability(client_tool_capability)

        filtered_tools: dict[str, Tool] = {}
        # 处理工具配置
        configured_tool_name_set, tool_configs = await self._resolve_tool_config(user_id, tools)
        for name, tool in tools.items():
            policy = tool.definition.policy
            # 如果一个工具没有被配置，那么它就不会被启用
            if (
                configured_tool_name_set is not None
                and tool.definition.config_spec is not None
                and name not in configured_tool_name_set
            ):
                continue

            # 明确暴露的工具，用于暴露 expose_by_default 为 False 的工具
            explicitly_exposed = name in expose_tool_name_set
            # 工具要求某个 builtin skill id，而本轮 allowed_skill_ids 满足它
            skill_exposure_satisfaction = (policy.required_allowed_builtin_skill_ids and
                             set(policy.required_allowed_builtin_skill_ids).issubset(set(context.get("allowed_skill_ids") or [])))

            if policy.selection_mode == ToolSelectionMode.CONTEXTUAL:
                # 对于 CONTEXTUAL 工具而言，只要用户没有显示禁用，即启用
                _is_tool_selected = tool_selection_overrides.get(name) is not False
            else:
                # 对于非 CONTEXTUAL 工具而言，受用户 tool_selection_default_enabled 和 tool_selection_overrides 的指定影响
                _is_tool_selected = bool(tool_selection_overrides.get(name, tool_selection_default_enabled))

            if not _is_tool_selected: # 未被选中，跳过
                continue

            if policy.required_allowed_builtin_skill_ids and not skill_exposure_satisfaction: # 暴露的技能不满足工具要求
                continue

            # CONTEXTUAL 工具必须靠 explicitly_exposed 显式启用 或 加载了要求的 Skill
            if policy.selection_mode == ToolSelectionMode.CONTEXTUAL:
                if explicitly_exposed or skill_exposure_satisfaction: filtered_tools[name] = tool
                continue

            # USER_SELECTABLE 但 expose_by_default 为 False 的工具必须靠 explicitly_exposed 显式启用 或 加载了要求的 Skill
            if not policy.expose_by_default:
                if explicitly_exposed or skill_exposure_satisfaction: filtered_tools[name] = tool
                continue
            filtered_tools[name] = tool

        return ToolScope(
            tools=filtered_tools,
            context=context,
            configs=tool_configs,
            client_tool_capabilities=client_tool_capabilities,
        )

    # 恢复当前工具推导（从暂停的任务缓存中）
    async def recover_derived(self, staging_data: dict[str, Any], user_id: str) -> ToolScope:
        tool_names = list(staging_data.get("tool_names") or [])
        client_tool_capabilities = list(staging_data.get("client_tool_capabilities") or [])
        tools = await self.system_tools()

        # 收集用户配置的 MCP 工具
        if self._mcp_tool_catalog is not None:
            for name, tool in (await self._mcp_tool_catalog.load_user_mcp_tools(user_id)).items():
                tools.setdefault(name, tool) # 用户 MCP 工具不覆盖已有工具

        # 构建前端工具
        for client_tool_capability in client_tool_capabilities:
            if client_tool_capability.name in tools:
                raise ServiceException(
                    ChatErrorCode.TOOL_CONFIG_INVALID, custom_msg=f"客户端工具名称冲突: {client_tool_capability.name}",
                )
            tools[client_tool_capability.name] = client_tool_from_capability(client_tool_capability)

        # 如果有此前存在的 Tool 现在未找到，则报错
        missing_names = [name for name in tool_names if name not in tools]
        if missing_names:
            raise ServiceException(
                ChatErrorCode.TOOL_NOT_FOUND,
                custom_msg=f"恢复对话所需工具不可用: {', '.join(missing_names)}",
            )

        selected_tools = {name: tools[name] for name in tool_names}

        return ToolScope(
            tools=selected_tools,
            context=dict(staging_data.get("context") or {}),
            configs=dict(staging_data.get("configs") or {}),
            client_tool_capabilities=client_tool_capabilities,
        )

    def __len__(self) -> int:
        return len(self._tools)

    async def _resolve_tool_config(self, user_id: str, tools: dict[str, Tool]) -> tuple[set[str], dict[str, dict[str, Any]]]:
        # 获取用户所有 Tool 配置
        configs = await self._tool_config_repo.list_tool_configs(user_id)
        configs = { config.tool_name: config for config in configs}

        configured_tool_names: set[str] = set() # 收集已经配置完整的 Tool name
        tool_configs: dict[str, dict[str, Any]] = {} # 收集配置

        for name, tool in tools.items():
            config_spec = tool.definition.config_spec
            if config_spec is None:
                continue  # 无需配置的跳过

            entity = configs.get(name)
            # 检查用户是否有配置、是否启用、必填项是否完整
            if entity is None or not entity.enabled: continue # 不满足的跳过
            missing: list[str] = []
            for key in config_spec.required_keys:
                source = entity.secret_config if key in config_spec.secret_keys else entity.config
                if source.get(key) is None or (isinstance(source.get(key), str) and not source.get(key).strip()):
                    missing.append(key)
            if missing: continue # 不满足的跳过

            # 合并普通配置和 secret 配置
            configured_tool_names.add(name)
            tool_configs[name] = {
                **{
                    key: value
                    for key, value in entity.config.items()
                },
                **{
                    key: value
                    for key, value in entity.secret_config.items()
                },
            }

        return configured_tool_names, tool_configs
