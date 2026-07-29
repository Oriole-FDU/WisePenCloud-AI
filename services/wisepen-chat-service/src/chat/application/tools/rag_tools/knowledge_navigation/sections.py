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
            "description": "Required. The state_id returned by knowledge_navigate_locate.",
        },
        "resource_id": {
            "type": "string",
            "minLength": 1,
            "description": "Required. The resource_id containing the selected sections.",
        },
        "section_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 12,
            "description": (
                "Required. Section IDs already returned by locate or an earlier "
                "section read. Their complete own content and tree frontier are returned."
            ),
        },
    },
    "required": ["state_id", "resource_id", "section_ids"],
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
                    "Read selected sections from a private document and reveal their "
                    "parent, previous, next, and child section frontier. Use section_ids "
                    "returned by knowledge_navigate_locate or this tool."
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
        resource_id = str(kwargs.get("resource_id", "")).strip()
        section_ids = tuple(
            str(value).strip() for value in kwargs.get("section_ids", ())
        )
        if (
            not state_id
            or not resource_id
            or not section_ids
            or any(not section_id for section_id in section_ids)
            or len(set(section_ids)) != len(section_ids)
        ):
            raise ToolExecutionError(
                reason="knowledge_navigation_invalid_request",
                detail_reason=(
                    "state_id, resource_id, and unique non-blank section_ids are required"
                ),
                retryable=False,
            )

        try:
            result = await self._service.read_sections(
                state_id=state_id,
                resource_id=resource_id,
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

        return _render_result(result, section_ids=section_ids)


def _render_result(
    result: KnowledgeSectionReadResult,
    *,
    section_ids: tuple[str, ...],
) -> ToolReturn:
    # 这里先初始化为空列表；section_view_payload 会通过可变参数把每个
    # reading block 和 evidence 的完整正文原地追加进来，并返回对应的 content_index。
    cacheable_texts: list[CacheableText] = []
    sections = [
        section_view_payload(section, cacheable_texts)
        for section in result.sections
    ]
    return ToolReturn(
        visible_result={
            "state_id": result.state.state_id,
            "action": "read_sections",
            "root_query": result.state.root_query,
            "focus": {"section_ids": list(section_ids)},
            "sections": sections,
            "navigation": {
                "visited_nodes": len(result.state.known_node_ids)
                + len(result.new_section_ids),
                "frontier_nodes": len(result.new_section_ids),
                "truncated": False,
                "exhausted": not result.new_section_ids,
            },
        },
        cacheable_texts=tuple(cacheable_texts),
    )
