from __future__ import annotations

from typing import Any

from chat.application.tools.common.tool_content_store import (
    ToolContentPutStatus,
    ToolContentReceipt,
    ToolContentStore,
)
from chat.application.tools.core.definition import ToolDefinition
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.tool_return import (
    CacheableText,
    ToolReturn,
)
from common.logger import warn


class ToolOutputCache:
    """将 ToolReturn 中可缓存的大文本内联，或转换为内容存储回执。"""

    __slots__ = ("_content_store", "_inline_max_chars")

    def __init__(
            self,
            *,
            content_store: ToolContentStore,
            inline_max_chars: int,
    ) -> None:
        if inline_max_chars < 1:
            raise ValueError("inline_max_chars must be greater than 0")

        self._content_store = content_store
        self._inline_max_chars = inline_max_chars

    async def process(
            self,
            *,
            tool_return: ToolReturn,
            invocation: ToolInvocation,
            tool_definition: ToolDefinition,
            session_id: str,
    ) -> dict[str, Any]:
        """将可缓存文本附加到可见结果，或替换为外部内容回执。"""
        payload = dict(tool_return.visible_result)

        # 与 ToolContentStore 的空文本规则保持一致，避免纯空白内容
        # 在 inline 和持久化两条路径中产生不同结果。
        cacheable_texts = tuple(
            cacheable_text
            for cacheable_text in tool_return.cacheable_texts
            if cacheable_text.text and not cacheable_text.text.isspace()
        )
        if not cacheable_texts:
            return payload

        # 总量较小时直接放入工具结果，避免一次额外的存储和读取。
        if (
                sum(len(cacheable_text.text) for cacheable_text in cacheable_texts)
                <= self._inline_max_chars
        ):
            payload["contents"] = tuple(
                cacheable_text.text for cacheable_text in cacheable_texts
            )
            return payload

        # 超过内联边界后，每段文本独立入库；单段失败不影响其他文本。
        receipts = await self._store_contents(
            invocation=invocation,
            cacheable_texts=cacheable_texts,
            tool_definition=tool_definition,
            session_id=session_id,
        )
        if receipts:
            payload["content_receipts"] = tuple(
                {
                    "content_id": receipt.content_id,
                    "chunk_count": receipt.chunk_count,
                    "supported_selectors": receipt.supported_selectors,
                }
                for receipt in receipts
            )

        return payload

    async def _store_contents(
            self,
            *,
            invocation: ToolInvocation,
            cacheable_texts: tuple[CacheableText, ...],
            tool_definition: ToolDefinition,
            session_id: str,
    ) -> tuple[ToolContentReceipt, ...]:
        """逐段存储大文本，并返回成功写入的内容回执。"""
        receipts: list[ToolContentReceipt] = []
        content_count = len(cacheable_texts)

        for index, cacheable_text in enumerate(cacheable_texts):
            try:
                result = await self._content_store.put(
                    session_id=session_id,
                    text=cacheable_text.text,
                    content_type=cacheable_text.content_type,
                    metadata={
                        "tool": invocation.tool_name,
                        "tool_call_id": invocation.tool_call_id,
                        "tool_arguments": invocation.tool_call_arguments,
                        "cacheable_text_index": index,
                        "cacheable_text_count": content_count,
                    },
                    chunked=tool_definition.policy.cache_chunked,
                )
            except Exception as exc:
                # 缓存属于附加能力，单段入库失败不应中断整个工具调用。
                warn(
                    "tool output cache content store failed.",
                    e=exc,
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.tool_call_id,
                    cacheable_text_index=index,
                    audit_message="工具输出部分内容入库失败，已继续处理其他可缓存文本。",
                )
                continue

            if result.receipt is not None:
                receipts.append(result.receipt)
            elif result.status is ToolContentPutStatus.CONTENT_TOO_LARGE:
                warn(
                    "tool output cache content too large.",
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.tool_call_id,
                    cacheable_text_index=index,
                    reason=result.reason,
                    audit_message="工具输出内容超过入库上限，该文本不会出现在模型输出回执中。",
                )

        return tuple(receipts)
