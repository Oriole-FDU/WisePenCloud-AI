from __future__ import annotations

from typing import Any

from chat.application.tools.common.tool_content_store import (
    ToolContentPutStatus,
    ToolContentReceipt,
    ToolContentStore,
)
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.tool_return import (
    CacheableText,
    ToolReturn,
)
from common.logger import warn

_TRUNCATION_MARKER = "\n...\n"


class ToolOutputCache:
    """将 ToolReturn 中可缓存的大文本存储，并生成模型可见的内容预览。"""

    __slots__ = ("_content_store", "_per_max_chars", "_total_max_chars")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
        per_max_chars: int,
        total_max_chars: int,
    ) -> None:
        if per_max_chars < 1:
            raise ValueError("per_max_chars must be greater than 0")
        if total_max_chars < 1:
            raise ValueError("total_max_chars must be greater than 0")

        self._content_store = content_store
        self._per_max_chars = per_max_chars
        self._total_max_chars = total_max_chars

    async def process(
        self,
        *,
        tool_return: ToolReturn,
        invocation: ToolInvocation,
        session_id: str,
    ) -> dict[str, Any]:
        """将可缓存文本附加到可见结果，并补充后续读取所需的凭证字段。"""
        payload = dict(tool_return.visible_result)

        # 与 ToolContentStore 的空文本规则保持一致，避免纯空白内容
        # 在预览和持久化两条路径中产生不同结果。
        cacheable_texts = tuple(
            cacheable_text
            for cacheable_text in tool_return.cacheable_texts
            if cacheable_text.text and not cacheable_text.text.isspace()
        )
        if not cacheable_texts:
            return payload

        # 每段内容都先入库，模型可见预览和后续读取凭证保持在同一个 content 条目里。
        receipts = dict(
            await self._store_contents(
                invocation=invocation,
                cacheable_texts=cacheable_texts,
                session_id=session_id,
            )
        )
        budget = self._preview_budget(cacheable_texts)
        payload["contents"] = tuple(
            self._content_payload(
                content_index=index,
                cacheable_text=cacheable_text,
                receipt=receipts.get(index),
                budget=budget,
            )
            for index, cacheable_text in enumerate(cacheable_texts)
        )
        return payload

    def _preview_budget(self, cacheable_texts: tuple[CacheableText, ...]) -> int:
        total_length = sum(len(cacheable_text.text) for cacheable_text in cacheable_texts)
        if total_length >= self._total_max_chars:
            return max(1, self._total_max_chars // len(cacheable_texts))
        return self._per_max_chars

    def _content_payload(
        self,
        *,
        content_index: int,
        cacheable_text: CacheableText,
        receipt: ToolContentReceipt | None,
        budget: int,
    ) -> dict[str, Any]:
        preview, truncated = _preview_text(cacheable_text.text, budget)
        item: dict[str, Any] = {
            "content_index": content_index,
            "text": preview,
            "truncated": truncated,
            "total_length": len(cacheable_text.text),
            "metadata": dict(cacheable_text.metadata),
        }
        if receipt is not None:
            item.update(
                {
                    "content_id": receipt.content_id,
                    "chunk_count": receipt.chunk_count,
                    "locator_count": receipt.locator_count,
                    "locator_kinds": receipt.locator_kinds,
                    "total_length": receipt.total_length,
                    "metadata": dict(receipt.metadata),
                }
            )
        return item

    async def _store_contents(
        self,
        *,
        invocation: ToolInvocation,
        cacheable_texts: tuple[CacheableText, ...],
        session_id: str,
    ) -> tuple[tuple[int, ToolContentReceipt], ...]:
        """逐段存储大文本，并返回成功写入的内容回执。"""
        receipts: list[tuple[int, ToolContentReceipt]] = []

        for index, cacheable_text in enumerate(cacheable_texts):
            try:
                result = await self._content_store.put(
                    session_id=session_id,
                    text=cacheable_text.text,
                    content_type=(
                        "text/markdown" if cacheable_text.is_md else "text/plain"
                    ),
                    metadata=dict(cacheable_text.metadata),
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
                receipts.append((index, result.receipt))
            elif result.status is ToolContentPutStatus.CONTENT_TOO_LARGE:
                warn(
                    "tool output cache content too large.",
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.tool_call_id,
                    cacheable_text_index=index,
                    reason=result.reason,
                    audit_message="工具输出内容超过入库上限，该文本不会带有后续读取凭证。",
                )

        return tuple(receipts)


def _preview_text(text: str, budget: int) -> tuple[str, bool]:
    if len(text) <= budget:
        return text, False
    if budget <= len(_TRUNCATION_MARKER):
        return text[:budget], True

    available = budget - len(_TRUNCATION_MARKER)
    head_chars = available - available // 2
    tail_chars = available // 2
    return (
        text[:head_chars] + _TRUNCATION_MARKER + text[-tail_chars:],
        True,
    )
