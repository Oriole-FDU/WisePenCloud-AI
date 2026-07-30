from __future__ import annotations

from typing import Any

from chat.application.rag.graph_extraction import KnowledgeRelationType
from chat.application.rag.knowledge_navigation import (
    KnowledgeNavigationDirection,
    KnowledgeNavigationEdge,
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
                "Required. Reuse the exact state_id returned by locate or an earlier "
                "expand call. It binds the allowed graph nodes and original locate "
                "intent to the current session."
            ),
        },
        "node_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 16,
            "description": (
                "Required. Graph node_ids to use as expansion starting points. They "
                "must have been returned by locate or expand under this state_id; do "
                "not pass section_ids. Multiple nodes explore from each selected "
                "starting concept in the same call."
            ),
        },
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional current multi-hop reasoning goal. Neo4j first generates "
                "candidate paths using node_ids, relation_types, direction, and "
                "max_depth; this query then ranks those candidates. Set it when the "
                "current hop seeks something more specific than the original locate "
                "question. Omit it to rank with the original locate query."
            ),
        },
        "relation_types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [item.value for item in KnowledgeRelationType],
            },
            "maxItems": 16,
            "description": (
                "Optional allowlist of semantic edge types. Relation names read from "
                "source to target: A DEPENDS_ON B means A depends on B; A PART_OF B "
                "means A is part of B; A CITES B means A cites B. RELATED_TO carries "
                "a more specific predicate in the result. Omit this parameter when "
                "the needed relation type is uncertain, because selecting types hides "
                "all other edges."
            ),
        },
        "direction": {
            "type": "string",
            "enum": [item.value for item in KnowledgeNavigationDirection],
            "default": "both",
            "description": (
                "Traversal direction relative to each selected node and the stored "
                "source -> target relation. Use 'out' to follow relations where the "
                "selected node is the source: from A on A DEPENDS_ON B, it discovers "
                "B. Use 'in' to find relations pointing to the selected node: from B "
                "on that same edge, it discovers A. Use 'both' when either role is "
                "useful. Direction changes path discovery only; it never reverses the "
                "meaning of the returned relation."
            ),
        },
        "max_depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2,
            "default": 1,
            "description": (
                "Maximum number of relations in a candidate path. Use 1 for direct "
                "neighbors. Use 2 only when the answer requires an intermediate "
                "concept; it explores a wider, noisier candidate pool before query "
                "ranking."
            ),
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 10,
            "description": (
                "Maximum number of ranked paths returned after candidate generation. "
                "This limits reasoning chains, not the number of nodes or source "
                "sections inside those paths."
            ),
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
                    "Description:\n"
                    "Follow concept, dependency, citation, and source relations from "
                    "nodes returned by knowledge_navigate_locate or an earlier expand "
                    "call. Requires the matching state_id and node_ids; use locate "
                    "when no navigation state exists.\n\n"
                    "Output:\n"
                    "Treat each path as one candidate reasoning chain: node_ids give "
                    "the concept order and edge_ids link the relations between them. "
                    "Use relation_type and predicate for structured relation reasoning, "
                    "but ground the answer in relation_evidence, which combines the "
                    "relation into a readable statement with its supporting quotes. "
                    "Use sources and their content_index references to inspect the "
                    "original document text before making a claim. Continue expansion "
                    "from newly relevant node_ids with the same state_id. If no paths "
                    "are present, the requested expansion found no supported relation "
                    "under the current constraints."
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
        query = str(kwargs.get("query", "")).strip() or None
        try:
            result = await self._service.expand(
                state_id=state_id,
                node_ids=node_ids,
                query=query,
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
        return _render_result(result)


def _render_result(result: KnowledgeNavigationExpandResult) -> ToolReturn:
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
    node_labels = {node.node_id: node.label for node in result.nodes}
    return ToolReturn(
        visible_result={
            "state_id": result.state_id,
            "nodes": [node.to_payload() for node in result.nodes],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "relation_type": edge.relation_type.value,
                    "predicate": edge.predicate,
                    "direction": edge_directions[edge.edge_id].value,
                    "relation_evidence": _relation_evidence(edge, node_labels),
                }
                for edge in result.edges
            ],
            "paths": [path.to_payload() for path in result.paths],
            "sources": sources,
        },
        cacheable_texts=tuple(cacheable_texts),
    )


def _relation_evidence(
    edge: KnowledgeNavigationEdge,
    node_labels: dict[str, str],
) -> str:
    source_label = node_labels.get(edge.source_node_id, edge.source_node_id)
    target_label = node_labels.get(edge.target_node_id, edge.target_node_id)
    relation = edge.relation_type.value
    if edge.predicate:
        relation = f"{relation} ({edge.predicate})"

    statement = f"{source_label} --{relation}--> {target_label}"
    quotes = tuple(dict.fromkeys(edge.evidence_quotes))
    if not quotes:
        return statement

    evidence = "\n".join(
        f"{index}. {quote}" for index, quote in enumerate(quotes, start=1)
    )
    return f"{statement}\nEvidence:\n{evidence}"
