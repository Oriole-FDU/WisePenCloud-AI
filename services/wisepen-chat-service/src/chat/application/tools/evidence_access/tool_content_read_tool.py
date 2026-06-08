from typing import Any, Dict, Optional

from chat.application.tools.tool_content_store import (
    ToolContentStore,
    read_tool_content_window_by_index,
    read_tool_content_window_by_offset,
)
from chat.core.config.app_settings import settings as app_settings
from chat.domain.interfaces.tool import BaseTool, ToolExposure
from common.logger import log_event

TOOL_DESCRIPTION = (
    "Reads a window of previously cached tool content by content_id. "
    "Use this for one known ToolContent window, either by sequential offset or by a ranked chunk_index.\n\n"
    "Use offset mode with content_id plus offset/limit to continue from next_offset.\n"
    "Use chunk_index mode with content_id plus chunk_index/before_chunks/after_chunks to inspect "
    "one ranked passage and its surrounding chunks.\n"
    "Use either offset mode or chunk_index mode, never both.\n"
    "Do not use tool_content_read for blind scanning when evidence_rank can rank the cached content.\n\n"
    "content_id values usually look like cnt_* and are not document file_refs.\n"
    "Never pass file_ref values to tool_content_read."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "cnt_* ToolContent identifier. Do not pass file_ref values.",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "Character offset for offset mode, usually next_offset from a previous window.",
            "default": 0,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum characters to return in offset mode.",
        },
        "chunk_index": {
            "type": "integer",
            "minimum": 0,
            "description": "Chunk index returned by evidence_rank for chunk_index mode.",
        },
        "before_chunks": {
            "type": "integer",
            "minimum": 0,
            "maximum": 3,
            "default": 1,
            "description": "Number of chunks before chunk_index to include.",
        },
        "after_chunks": {
            "type": "integer",
            "minimum": 0,
            "maximum": 3,
            "default": 1,
            "description": "Number of chunks after chunk_index to include.",
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentReadTool(BaseTool):
    """
    长工具输出续读的门面网关运行时适配器。
    """

    def __init__(self, *, content_store: ToolContentStore) -> None:
        """初始化工具内容读取工具。"""
        self._content_store = content_store

    @property
    def name(self) -> str:
        return "tool_content_read"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return TOOL_SCHEMA

    @property
    def exposure(self) -> ToolExposure:
        return ToolExposure.BOOTSTRAP

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("evidence_access",)

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        content_id = kwargs.get("content_id")
        if not isinstance(content_id, str) or not content_id:
            return "[Tool Error] content_id must be a non-empty string."
        if content_id.strip() != content_id:
            return "[Tool Error] content_id must not contain leading or trailing whitespace."
        if content_id.startswith("file_ref"):
            return "[Tool Error] content_id must be a cnt_* value, not a file_ref value."

        canonical_content_id, redirect_note = self._content_store.canonicalize_content_id(
            content_id=content_id,
            session_id=session_id,
        )
        content_id = canonical_content_id

        # 路由特征码提取
        has_chunk_index = "chunk_index" in kwargs
        has_offset_mode = "offset" in kwargs or "limit" in kwargs
        has_context_args = "before_chunks" in kwargs or "after_chunks" in kwargs

        # 刚性协议冲突拦截
        if has_chunk_index and has_offset_mode:
            return "[Tool Error] Use either chunk_index mode or offset mode, not both."
        if has_context_args and not has_chunk_index:
            return "[Tool Error] before_chunks/after_chunks require chunk_index mode."

        # ----------------------------------------------------------------
        # 模式一：基于精排 Chunk Index 辐射的邻域视窗读取器
        # ----------------------------------------------------------------
        if has_chunk_index:
            chunk_index = kwargs.get("chunk_index")
            if not isinstance(chunk_index, int) or chunk_index < 0:
                return "[Tool Error] chunk_index must be an integer greater than or equal to 0."

            before_chunks = kwargs.get("before_chunks", 1)
            after_chunks = kwargs.get("after_chunks", 1)

            if not isinstance(before_chunks, int) or not (0 <= before_chunks <= 3):
                return "[Tool Error] before_chunks must be an integer between 0 and 3."
            if not isinstance(after_chunks, int) or not (0 <= after_chunks <= 3):
                return "[Tool Error] after_chunks must be an integer between 0 and 3."

            log_event(
                "tool_content_read 调用",
                content_id=content_id,
                chunk_index=chunk_index,
                before_chunks=before_chunks,
                after_chunks=after_chunks,
            )

            result = read_tool_content_window_by_index(
                session_id=session_id,
                content_id=content_id,
                chunk_index=chunk_index,
                before_chunks=before_chunks,
                after_chunks=after_chunks,
                content_store=self._content_store,
            )
            return _prepend_redirect_note(result, redirect_note)

        # ----------------------------------------------------------------
        # 模式二：基于绝对字符偏移量的单图流式续读器
        # ----------------------------------------------------------------
        offset = kwargs.get("offset", 0)
        if not isinstance(offset, int) or offset < 0:
            return "[Tool Error] offset must be an integer greater than or equal to 0."

        limit = kwargs.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 1:
                return "[Tool Error] limit must be an integer greater than or equal to 1."
            max_limit = app_settings.TOOL_RESULT_MAX_CHARS * 2
            if limit > max_limit:
                return f"[Tool Error] limit must be less than or equal to {max_limit}."

        log_event(
            "tool_content_read 调用",
            content_id=content_id,
            offset=offset,
            limit=limit,
        )

        result = read_tool_content_window_by_offset(
            session_id=session_id,
            content_id=content_id,
            offset=offset,
            limit=limit,
            content_store=self._content_store,
        )
        return _prepend_redirect_note(result, redirect_note)


def _prepend_redirect_note(result: str, redirect_note: Optional[str]) -> str:
    if not redirect_note:
        return result
    return f"[ToolContent Auto-Redirect]\n{redirect_note}\n\n{result}"
