from datetime import datetime
from typing import Dict, Any, Optional

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.domain.repositories import MessageRepository
from common.logger import error


class GetHistoricalChatMessagesTool:
    """
    历史消息全文检索工具。
    Schema 中不暴露 session_id，该字段由系统通过 context 强注入，防止 LLM 幻觉伪造导致越权访问。
    """

    def __init__(self, message_repo: MessageRepository, max_output_chars: int) -> None:
        self._message_repo = message_repo
        self._max_output_chars = max_output_chars
        # session_id 故意不暴露，由系统通过 context 注入
        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The keyword or phrase to search for in message history. The keyword argument must be in the same language as the user's query.",
                },
                "start_time": {
                    "type": "string",
                    "description": "ISO 8601 start time for filtering messages (optional).",
                },
                "end_time": {
                    "type": "string",
                    "description": "ISO 8601 end time for filtering messages (optional).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of results to tool_return. Defaults to 10.",
                    "default": 10,
                },
            },
            "required": ["keyword"],
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="get_historical_chat_messages",
                description=(
                    "Search historical chat messages in the current session by keyword and optional time range.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when the user asks to recall specific facts, events, or details from earlier in the chat that are no longer in the current context window.\n"
                    "  - SHOULD trigger when the user references 'what we discussed before', 'earlier you said', or similar phrases.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - The information is already in the current context window.\n"
                    "  - The user asks about external content — use web_search or web_fetch instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - keyword MUST be in the same language as the user's chat language; otherwise the search may fail.\n"
                    "  - start_time and end_time are optional ISO 8601 timestamps for filtering.\n"
                    "  - limit defaults to 10.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns matching messages with role and timestamp.\n"
                    "  - If no results are found, retry with a different keyword language before giving up.\n"
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=5.0,
                max_output_chars=max_output_chars,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> str:
        # session_id 从系统注入的 context 读取
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            raise ToolExecutionError(
                reason="missing_session_id",
                detail_reason="Missing session_id in execution context.",
            )

        keyword: str = kwargs.get("keyword", "").strip()
        if not keyword:
            raise ToolExecutionError(
                reason="missing_keyword",
                detail_reason="Missing required argument: keyword.",
            )

        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None
        try:
            if kwargs.get("start_time"):
                start_time = datetime.fromisoformat(kwargs["start_time"])
            if kwargs.get("end_time"):
                end_time = datetime.fromisoformat(kwargs["end_time"])
        except ValueError:
            pass  # 非法时间格式，静默忽略，不中断检索

        limit = int(kwargs.get("limit", 10))

        try:
            results = await self._message_repo.search_messages_by_text(keyword=keyword, session_id=session_id,
                                                                       start_time=start_time, end_time=end_time,
                                                                       limit=limit)
        except Exception as e:
            error("history message full text search failed.", session_id=session_id, keyword=keyword, e=e)
            raise ToolExecutionError(
                reason="history_search_failed",
                detail_reason=f"Search failed: {type(e).__name__}",
                retryable=True,
                metadata={"detail": str(e)},
            ) from e

        if not results:
            return f"[Got Historical Chat Messages] No historical chat message found for keyword: '{keyword}'."

        raw = "[Got Historical Chat Messages]\n".join(
            [f"-(role={m.role.value} created={m.created_at.isoformat()}): {m.content}" for m in results]
        )

        # 字符截断，防止超长结果在后续迭代中撑爆上下文水位
        if len(raw) > self._max_output_chars:
            raw = raw[:self._max_output_chars] + "\n...[truncated]"

        return raw


