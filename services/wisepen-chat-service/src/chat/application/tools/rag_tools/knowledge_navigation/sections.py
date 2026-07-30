from __future__ import annotations

from typing import Any

from chat.application.rag.knowledge_navigation import (
    KnowledgeNavigationService,
    KnowledgeNavigationStateInvalidatedError,
    KnowledgeNavigationStateNotFoundError,
    KnowledgeSectionReadResult,
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
                "Required. Reuse the exact state_id returned by locate or a previous "
                "navigation call. It binds the already exposed sections to the current "
                "session, so an ID from another navigation state cannot be substituted."
            ),
        },
        "section_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 12,
            "description": (
                "Required. Select section_ids already exposed in sources or a section "
                "frontier under this state; arbitrary document IDs are not accepted. "
                "Each selected section returns its own content plus its parent, previous, "
                "next, and child choices. Pass multiple IDs when those branches should "
                "be read together."
            ),
        },
    },
    "required": ["state_id", "section_ids"],
    "additionalProperties": False,
}


class KnowledgeNavigateSectionsTool:
    """读取标题树节点正文，并继续展开统一文档内部结构。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: KnowledgeNavigationService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="knowledge_navigate_sections",
                description=(
                    "Description:\n"
                    "Read selected private-document sections and reveal their parent, "
                    "previous, next, and child frontier. Use section_ids returned by "
                    "knowledge_navigate_locate or this tool.\n\n"
                    "Output:\n"
                    "Use each section's reading blocks and evidence as the current "
                    "document context, resolving content_index through "
                    "contents/content_receipts when exact wording is needed. The "
                    "frontier describes where to read next: choose parent for broader "
                    "context, previous or next for sequence, and children for detail, "
                    "then call this tool again with those section_ids. Structural "
                    "adjacency is for navigation and does not itself prove a semantic "
                    "relation. Reuse state_id for the continuation."
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
        section_ids = tuple(
            str(value).strip() for value in kwargs.get("section_ids", ())
        )
        if (
            not state_id
            or not section_ids
            or any(not section_id for section_id in section_ids)
            or len(set(section_ids)) != len(section_ids)
        ):
            raise ToolExecutionError(
                reason="knowledge_navigation_invalid_request",
                detail_reason="state_id and unique non-blank section_ids are required",
                retryable=False,
            )

        try:
            result = await self._service.read_sections(
                state_id=state_id,
                section_ids=section_ids,
                session_id=str(context["session_id"]),
                permission_scope=RagPermissionScope(
                    user_id=str(context["user_id"]),
                    group_role_map=dict(context["group_role_map"]),
                ),
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


def _render_result(result: KnowledgeSectionReadResult) -> ToolReturn:
    cacheable_texts: list[CacheableText] = []
    sections = [
        section_view_payload(section, cacheable_texts)
        for section in result.sections
    ]
    return ToolReturn(
        visible_result={
            "state_id": result.state_id,
            "sections": sections,
        },
        cacheable_texts=tuple(cacheable_texts),
    )
