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
from chat.core.config.app_settings import settings
from chat.domain.repositories import MessageRepository, SessionRepository


class GetSessionMessagesTool:
    def __init__(self, session_repo: SessionRepository, message_repo: MessageRepository) -> None:
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="get_session_messages",
                description="Read messages of one of the current user's chat sessions by id. Use list_sessions first to discover ids.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Session id to read."},
                        "page": {"type": "integer", "default": 1, "description": "Page of turns; 1 is the latest."},
                        "size": {"type": "integer", "default": 20},
                    },
                    "required": ["session_id"],
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
            ui_spec=ToolUISpec(display_name="读取会话消息", description="读取某个会话的历史消息。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        session_id = str(kwargs.get("session_id") or "").strip()
        if not session_id:
            raise ToolExecutionError(reason="invalid_session", detail_reason="session_id is required.")
        # 归属校验：只能读当前用户自己的会话
        await self._session_repo.get_session_for_user(session_id, user_id)
        page = int(kwargs.get("page") or 1)
        size = int(kwargs.get("size") or 20)
        messages, total_turns = await self._message_repo.list_session_message_turns_page(session_id, page=page, size=size)
        if not messages:
            return "No messages in this session."
        lines = [f"Session {session_id} messages (page={page} total_turns={total_turns}):"]
        for m in messages:
            lines.append(f"- ({m.role.value}, {m.created_at.isoformat()}): {m.content}")
        return "\n".join(lines)
