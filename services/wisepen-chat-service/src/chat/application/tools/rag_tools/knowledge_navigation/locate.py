from __future__ import annotations

from typing import Any

from chat.application.rag.knowledge_navigation import (
    KnowledgeNavigationLocateResult,
    KnowledgeNavigationService,
)
from chat.application.rag.retrieval import RagPermissionScope, RagRetrievalError
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn

from .common import navigation_backend_error, section_view_payload

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. The complete question or concept to locate in the user's "
                "private documents. Do not use keywords or describe the retrieval task."
            ),
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 10,
            "description": "Maximum number of located sources to return.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class KnowledgeNavigateLocateTool:
    """在当前用户可读的私有资料中定位证据并创建导航状态。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: KnowledgeNavigationService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="knowledge_navigate_locate",
                description=(
                    "Locate evidence and concepts in the user's private WisePen "
                    "documents. Use this for the first private-knowledge query. It "
                    "returns readable source context, graph nodes, and a state_id. "
                    "Continue from returned nodes with knowledge_navigate_expand."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("user_id", "session_id", "group_role_map"),
                timeout_seconds=300.0,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolReturn:
        del config
        query = str(kwargs.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(
                reason="knowledge_navigation_invalid_request",
                detail_reason="query must not be blank",
                retryable=False,
            )
        permission_scope = RagPermissionScope(
            user_id=str(context["user_id"]),
            group_role_map=dict(context["group_role_map"]),
        )
        try:
            result = await self._service.locate(
                query=query,
                max_results=int(kwargs.get("max_results", 10)),
                session_id=str(context["session_id"]),
                permission_scope=permission_scope,
            )
        except RagRetrievalError as error:
            raise ToolExecutionError(
                reason="knowledge_navigation_invalid_request",
                detail_reason=str(error),
                retryable=False,
            ) from error
        except Exception as error:
            raise navigation_backend_error(error) from error
        return _render_result(result)


def _render_result(result: KnowledgeNavigationLocateResult) -> ToolReturn:
    # 这里先初始化为空列表；section_view_payload 会通过可变参数把每个
    # source 的完整正文原地追加进来，同时在 payload 中记录对应的 content_index。
    cacheable_texts: list[CacheableText] = []
    sources = [
        section_view_payload(source, cacheable_texts) for source in result.sources
    ]
    return ToolReturn(
        visible_result={
            "state_id": result.state.state_id,
            "action": "locate",
            "root_query": result.state.root_query,
            "focus": {
                "query": result.state.root_query,
                "node_ids": [node.node_id for node in result.nodes],
            },
            "nodes": [node.to_payload() for node in result.nodes],
            "edges": [],
            "paths": [],
            "sources": sources,
            "navigation": {
                "visited_nodes": 0,
                "frontier_nodes": len(result.nodes),
                "truncated": False,
                "exhausted": not sources,
            },
        },
        cacheable_texts=tuple(cacheable_texts),
    )
