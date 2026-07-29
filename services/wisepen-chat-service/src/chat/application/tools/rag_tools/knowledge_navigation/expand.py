from __future__ import annotations

from typing import Any

from chat.application.rag.graph_extraction import KnowledgeRelationType
from chat.application.rag.knowledge_navigation import (
    KnowledgeNavigationDirection,
    KnowledgeNavigationExpandResult,
    KnowledgeNavigationService,
    KnowledgeNavigationStateInvalidatedError,
    KnowledgeNavigationStateNotFoundError,
)
from chat.application.rag.retrieval import RagPermissionScope
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
        "state_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. A state_id returned by knowledge_navigate_locate or an "
                "earlier knowledge_navigate_expand call in this session."
            ),
        },
        "node_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 16,
            "description": (
                "Required. One or more node_ids returned under the same state_id."
            ),
        },
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Optional current reading focus recorded in the result.",
        },
        "relation_types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [item.value for item in KnowledgeRelationType],
            },
            "maxItems": 16,
            "description": "Optional relation types to follow. Omit to follow any relation.",
        },
        "direction": {
            "type": "string",
            "enum": [item.value for item in KnowledgeNavigationDirection],
            "default": "both",
            "description": "Traversal direction relative to each selected node.",
        },
        "max_depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2,
            "default": 1,
            "description": "Maximum graph hops. Use 1 unless a second hop is needed.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 10,
            "description": "Maximum number of expanded paths to return.",
        },
    },
    "required": ["state_id", "node_ids"],
    "additionalProperties": False,
}


class KnowledgeNavigateExpandTool:
    """从当前会话已返回的知识节点继续跨文档导航。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: KnowledgeNavigationService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="knowledge_navigate_expand",
                description=(
                    "Follow concept, dependency, citation, and source relations from "
                    "nodes returned by knowledge_navigate_locate or an earlier expand "
                    "call. Requires the matching state_id and node_ids. Use locate "
                    "instead when no navigation state exists."
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
        state_id = str(kwargs.get("state_id", "")).strip()
        node_ids = tuple(str(value).strip() for value in kwargs.get("node_ids", ()))
        if (
            not state_id
            or not node_ids
            or any(not node_id for node_id in node_ids)
            or len(set(node_ids)) != len(node_ids)
        ):
            raise ToolExecutionError(
                reason="knowledge_navigation_invalid_request",
                detail_reason="state_id and unique non-blank node_ids are required",
                retryable=False,
            )
        permission_scope = RagPermissionScope(
            user_id=str(context["user_id"]),
            group_role_map=dict(context["group_role_map"]),
        )
        max_results = int(kwargs.get("max_results", 10))
        try:
            result = await self._service.expand(
                state_id=state_id,
                node_ids=node_ids,
                relation_types=tuple(
                    KnowledgeRelationType(value)
                    for value in kwargs.get("relation_types", ())
                ),
                direction=KnowledgeNavigationDirection(
                    kwargs.get("direction", KnowledgeNavigationDirection.BOTH.value)
                ),
                max_depth=int(kwargs.get("max_depth", 1)),
                max_results=max_results,
                session_id=str(context["session_id"]),
                permission_scope=permission_scope,
            )
        except KnowledgeNavigationStateNotFoundError as error:
            raise ToolExecutionError(
                reason="knowledge_navigation_state_not_found",
                retryable=False,
            ) from error
        except KnowledgeNavigationStateInvalidatedError as error:
            raise ToolExecutionError(
                reason="knowledge_navigation_state_invalidated",
                retryable=False,
            ) from error
        except Exception as error:
            raise navigation_backend_error(error) from error
        return _render_result(
            result,
            node_ids=node_ids,
            query=(str(kwargs["query"]).strip() if "query" in kwargs else None),
            max_results=max_results,
        )


def _render_result(
    result: KnowledgeNavigationExpandResult,
    *,
    node_ids: tuple[str, ...],
    query: str | None,
    max_results: int,
) -> ToolReturn:
    # 这里先初始化为空列表；section_view_payload 会通过可变参数把每个
    # source 的完整正文原地追加进来，同时在 payload 中记录对应的 content_index。
    cacheable_texts: list[CacheableText] = []
    sources = [
        section_view_payload(source, cacheable_texts) for source in result.sources
    ]
    edge_directions = {}
    for path in result.paths:
        for index, edge in enumerate(path.edges):
            edge_directions.setdefault(
                edge.edge_id,
                (
                    KnowledgeNavigationDirection.OUT
                    if edge.source_node_id == path.nodes[index].node_id
                    else KnowledgeNavigationDirection.IN
                ),
            )
    return ToolReturn(
        visible_result={
            "state_id": result.state.state_id,
            "action": "expand",
            "root_query": result.state.root_query,
            "focus": {"query": query, "node_ids": list(node_ids)},
            "nodes": [node.to_payload() for node in result.nodes],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "relation_type": edge.relation_type.value,
                    "relation_profile": edge.relation_profile.value,
                    "predicate": edge.predicate,
                    "direction": edge_directions[edge.edge_id].value,
                    "evidence_ref_ids": list(edge.evidence_ref_ids),
                    "qualifiers": [],
                }
                for edge in result.edges
            ],
            "paths": [path.to_payload() for path in result.paths],
            "sources": sources,
            "navigation": {
                "visited_nodes": len(result.state.known_node_ids)
                + len(result.new_node_ids),
                "frontier_nodes": len(result.new_node_ids),
                "truncated": len(result.paths) >= max_results,
                "exhausted": not result.paths,
            },
        },
        cacheable_texts=tuple(cacheable_texts),
    )
