from __future__ import annotations

from chat.application.tools.core.definition import OutputCacheStrategy
from chat.application.tools.core.execution.result import ToolOutput
from chat.application.tools.core.output_cache.cache_item import ToolOutputItemsCache
from chat.application.tools.core.output_cache.cache_long_text import ToolOutputLongTextCache
from chat.application.tools.core.output_cache.cache_store import ToolContentStore


class ToolOutputCache:
    def __init__(self, *, tool_content_store: ToolContentStore) -> None:
        self._items = ToolOutputItemsCache(tool_content_store=tool_content_store)
        self._long_text = ToolOutputLongTextCache(tool_content_store=tool_content_store)

    async def cache_tool_output(
        self,
        output: ToolOutput,
        *,
        strategy: OutputCacheStrategy,
        session_id: str | None,
        paths: tuple[str, ...] = (),
    ) -> ToolOutput:
        if not session_id:
            return output
        # 不透明输出
        if strategy is OutputCacheStrategy.OPAQUE:
            return await self._long_text.cache_long_output(output, session_id=session_id)
        # 长文本
        if strategy is OutputCacheStrategy.LONG_TEXT:
            return await self._long_text.cache_long_texts(output, paths=paths, session_id=session_id)
        # 条目序列（文本行 / 代码 / 日志 / 列表）
        if strategy is OutputCacheStrategy.ITEMS:
            return await self._items.cache_items(output, paths=paths, session_id=session_id)
        return output
