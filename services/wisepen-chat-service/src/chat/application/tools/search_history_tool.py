from datetime import datetime
from typing import Dict, Any, Optional
from common.logger import log_error

from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from chat.domain.repositories import MessageRepository


class SearchHistoricalMessagesTool(BaseTool):
    """
    历史消息全文检索工具。
    Schema 中不暴露 session_id，该字段由系统通过 context 强注入，防止 LLM 幻觉伪造导致越权访问。
    """

    def __init__(self, message_repo: MessageRepository) -> None:
        self._message_repo = message_repo

    @property
    def name(self) -> str:
        return "search_historical_messages"

    @property
    def description(self) -> str:
        return (
            "Search historical chat messages by keyword and optional time range. "
            "Use this when you need to recall specific facts, events, or details "
            "from earlier in the conversation that may not be in the current context window."
            "NOTE that the search keyword's language should match the user's chat language; otherwise, the search may fail. If no results are found, consider switching the keyword's language."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        # session_id 故意不暴露，由系统通过 context 注入
        return {
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

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        # session_id 从系统注入的 context 读取
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        keyword: str = kwargs.get("keyword", "").strip()
        if not keyword:
            return "[Tool Error] Missing required argument: keyword."

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
            results = await self._message_repo.full_text_search(
                keyword=keyword,
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        except Exception as e:
            log_error("历史消息全文检索", e, session=session_id, keyword=keyword)
            return f"[Tool Error] Search failed: {e}"

        if not results:
            return f"[Tool Result] No messages found for keyword: '{keyword}'."

        lines = [f"[{m.role.value}] ({m.created_at.isoformat()}): {m.content}" for m in results]
        raw = "\n".join(lines)

        # 字符截断，防止超长结果在后续迭代中撑爆上下文水位
        if len(raw) > settings.TOOL_RESULT_MAX_CHARS:
            raw = raw[:settings.TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"

        return raw

