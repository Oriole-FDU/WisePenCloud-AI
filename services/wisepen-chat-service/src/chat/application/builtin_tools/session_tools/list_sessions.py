from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository


class ListSessionsTool:
    def __init__(self, session_repo: SessionRepository) -> None:
        self._session_repo = session_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_sessions",
                description="List the current user's chat sessions. Use this to discover session ids when the user references another conversation.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "default": 1},
                        "size": {"type": "integer", "default": 20},
                    },
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
                max_output_chars=settings.tools.result_max_chars,
            ),
            ui_spec=ToolUISpec(display_name="列出会话", description="分页查看当前用户的聊天会话。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        page = int(kwargs.get("page") or 1)
        size = int(kwargs.get("size") or 20)
        sessions, total = await self._session_repo.list_sessions_for_user(user_id, page=page, size=size)
        if not sessions:
            return "No sessions found."
        lines = [f"Sessions (total={total}):"]
        for s in sessions:
            lines.append(f"- id={s.id} title={s.title} pinned={s.is_pinned} updated={s.updated_at.isoformat()}")
        return "\n".join(lines)
