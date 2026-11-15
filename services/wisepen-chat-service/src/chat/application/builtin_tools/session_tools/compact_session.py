from typing import Any

from chat.application.agents import AgentResolver
from chat.application.chat_context_assembler import ChatContextAssembler
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.interfaces.context_compact import ContextCompactor
from chat.domain.repositories import SessionRepository


class CompactSessionTool:
    def __init__(
        self,
        session_repo: SessionRepository,
        agent_resolver: AgentResolver,
        context_compactor: ContextCompactor,
        context_assembler: ChatContextAssembler,
    ) -> None:
        self._session_repo = session_repo
        self._agent_resolver = agent_resolver
        self._context_compactor = context_compactor
        self._context_assembler = context_assembler
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="compact_session",
                description=(
                    "Compact (summarize) the current session's chat history now. "
                    "This rebuilds the session summary and clears the hot context so the NEXT turn starts "
                    "with a shorter context; the current turn's already-loaded context is not changed. "
                    "Use this when the conversation is getting too long and will continue in later turns."
                ),
                parameters_schema=ToolParametersSchema({"type": "object", "properties": {}}),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id", "session_id"),
                timeout_seconds=60.0,
            ),
            ui_spec=ToolUISpec(display_name="压缩当前会话", description="立即压缩当前会话上下文，下一轮生效。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")
        if not session_id:
            raise ToolExecutionError(reason="invalid_session", detail_reason="session_id is missing.")
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        agent = await self._agent_resolver.resolve(session.agent_id, session.agent_version)
        if agent is None:
            raise ToolExecutionError(reason="agent_not_found", detail_reason="Session agent not found.")
        memory_policy = agent.spec.memory_policy
        if not (memory_policy.enable_chat_memory and memory_policy.enable_chat_memory_summary):
            return "This session's agent does not enable chat memory summary; nothing to compact."
        history = await self._context_assembler.get_chat_history_record_messages(session_id)
        await self._context_compactor.compact(
            session_id=session_id,
            history=history,
            existing_summary=session.current_summary,
            summary_prompt=memory_policy.summary_prompt,
        )
        await self._session_repo.update_context_tokens(session_id, 0)
        return "Session context compacted for the next turn."
