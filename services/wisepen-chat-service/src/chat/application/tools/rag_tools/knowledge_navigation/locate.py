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
                "Required. The full question or concept to answer from the user's "
                "private documents. Include the subject and any constraints needed to "
                "judge relevance. Use natural language, not search keywords or an "
                "instruction such as 'search my documents'. This also becomes the "
                "default ranking intent for later graph expansion."
            ),
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 10,
            "description": (
                "Maximum number of initially matched source sections. Use fewer for a "
                "focused question and more when the question is broad or ambiguous."
            ),
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
                    "Description:\n"
                    "Locate evidence and concepts in the user's private WisePen "
                    "documents. Use this as the first private-knowledge navigation "
                    "call.\n\n"
                    "Output:\n"
                    "Treat sources as the evidence available for the current question: "
                    "use their previews to judge relevance and content_index to access "
                    "the exact text through contents/content_receipts. Nodes are graph "
                    "navigation anchors, not evidence by themselves; select a relevant "
                    "node_id and call knowledge_navigate_expand when the question needs "
                    "cross-document or multi-hop reasoning. Call "
                    "knowledge_navigate_sections with a returned section_id when the "
                    "answer needs surrounding, preceding, or more detailed document "
                    "context. Reuse the returned state_id for either continuation."
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
    cacheable_texts: list[CacheableText] = []
    sources = [
        section_view_payload(source, cacheable_texts) for source in result.sources
    ]
    return ToolReturn(
        visible_result={
            "state_id": result.state_id,
            "nodes": [node.to_payload() for node in result.nodes],
            "sources": sources,
        },
        cacheable_texts=tuple(cacheable_texts),
    )
