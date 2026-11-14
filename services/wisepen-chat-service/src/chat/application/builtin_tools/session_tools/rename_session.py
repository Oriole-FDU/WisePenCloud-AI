from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.repositories import SessionRepository


class RenameSessionTool:
    def __init__(self, session_repo: SessionRepository) -> None:
        self._session_repo = session_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="rename_session",
                description="Rename a chat session. Omitting session_id renames the current session.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Optional; defaults to the current session."},
                        "new_title": {"type": "string", "description": "New session title."},
                    },
                    "required": ["new_title"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id", "session_id"),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="重命名会话", description="重命名会话标题。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        session_id = str(kwargs.get("session_id") or context.get("session_id") or "").strip()
        new_title = str(kwargs.get("new_title") or "").strip()
        if not session_id or not new_title:
            raise ToolExecutionError(reason="invalid_session", detail_reason="session_id and new_title are required.")
        session = await self._session_repo.rename_session(session_id, user_id, new_title)
        return f"Session renamed: id={session.id} title={session.title}."
