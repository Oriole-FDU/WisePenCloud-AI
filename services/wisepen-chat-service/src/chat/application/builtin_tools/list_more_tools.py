from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)


class ListMoreToolsTool:
    """逃生口：按类别动态加载本轮尚未暴露的工具

    路由未命中的类别不会出现在本轮 ToolScope 里，模型可先用本工具发现可加载的类别，
    再按类别把它们加载进来，供后续步骤调用。
    """

    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_more_tools",
                description=(
                    "Load additional tools from a category that is not currently available this turn. "
                    "Use this when the task needs a tool that is missing from the current tool list. "
                    "Pass the category name; call it without arguments to list the categories that can still be loaded. "
                    "Returns the tools that become available after this call."
                ),
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "工具类别名；省略时返回可加载的类别及其工具数",
                        },
                    },
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                timeout_seconds=5.0,
            ),
            ui_spec=ToolUISpec(display_name="加载更多工具", description="按类别动态加载本轮尚未暴露的工具。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    # 特殊工具 list_more_tools，这里没有 execute 方法
    async def list_more_tools(
        self,
        scope: Any,  # ToolScope，由 ToolExecutor 注入
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        category = str(kwargs.get("category") or "").strip()
        if not category:
            return {"categories": scope.available_categories()}
        tools = scope.expose_category(category)
        return {"category": category, "loaded": len(tools), "tools": tools}
