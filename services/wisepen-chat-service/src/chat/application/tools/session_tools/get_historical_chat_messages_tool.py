from datetime import datetime
from typing import Dict, Any, Optional
from common.logger import error

from chat.core.config.app_settings import settings
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.domain.repositories import MessageRepository


class GetHistoricalChatMessagesTool:
    """
    历史消息全文检索工具。
    Schema 中不暴露 session_id，该字段由系统通过 context 强注入，防止 LLM 幻觉伪造导致越权访问。
    """

    def __init__(self, message_repo: MessageRepository) -> None:
        self._message_repo = message_repo
        # session_id 故意不暴露，由系统通过 context 注入
        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
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
                    "description": "Maximum number of results to return. Defaults to 10.",
                    "default": 10,
                },
            },
            "required": ["keyword"],
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="get_historical_chat_messages",
                description=(
                    "Get historical chat messages by keyword and optional time range. "
                    "Use this when you need to recall specific facts, events, or details "
                    "from earlier in the chat that may not be in the current context window."
                    "NOTE that the search keyword's language should match the user's chat language; otherwise, the search may fail. "
                    "If no results are found, consider switching the keyword's language."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=5.0,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
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
            error("history message full text search failed.", session_id=session_id, keyword=keyword, exc=e)
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
        if len(raw) > settings.TOOL_RESULT_MAX_CHARS:
            raw = raw[:settings.TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"

        return raw


