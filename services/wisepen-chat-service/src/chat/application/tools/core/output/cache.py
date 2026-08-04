from __future__ import annotations

from typing import Any

from chat.application.tools.common.canonical_token_budget import (
    bounded_canonical_token_count,
    canonical_preview,
)
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


class ToolOutputCache:
    """将 ToolReturn 中可缓存的大文本存储，并生成模型可见的内容预览。"""

    __slots__ = ("_content_store", "_per_token_budget", "_total_token_budget")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
        per_token_budget: int,
        total_token_budget: int,
    ) -> None:
        if per_token_budget < 1:
            raise ValueError("per_token_budget must be greater than 0")
        if total_token_budget < 1:
            raise ValueError("total_token_budget must be greater than 0")

        self._content_store = content_store
        self._per_token_budget = per_token_budget
        self._total_token_budget = total_token_budget

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
        budgets = self._preview_budgets(cacheable_texts)
        payload["contents"] = tuple(
            self._content_payload(
                content_index=index,
                cacheable_text=cacheable_text,
                receipt=receipts.get(index),
                budget=budgets[index],
            )
            for index, cacheable_text in enumerate(cacheable_texts)
        )
        return payload

    def _preview_budgets(
        self,
        cacheable_texts: tuple[CacheableText, ...],
    ) -> tuple[int, ...]:
        desired = tuple(
            bounded_canonical_token_count(item.text, self._per_token_budget)
            for item in cacheable_texts
        )
        if sum(desired) <= self._total_token_budget:
            return desired

        budgets = [0] * len(desired)
        remaining = self._total_token_budget
        ordered = sorted(range(len(desired)), key=desired.__getitem__)
        for position, index in enumerate(ordered):
            pending = len(ordered) - position
            fair_share = remaining // pending
            if desired[index] <= fair_share:
                budgets[index] = desired[index]
                remaining -= desired[index]
                continue
            for pending_index in ordered[position:]:
                budgets[pending_index] = fair_share
            for pending_index in ordered[position : position + remaining % pending]:
                budgets[pending_index] += 1
            break
        return tuple(budgets)

    def _content_payload(
        self,
        *,
        content_index: int,
        cacheable_text: CacheableText,
        receipt: ToolContentReceipt | None,
        budget: int,
    ) -> dict[str, Any]:
        preview, truncated = canonical_preview(cacheable_text.text, budget)
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
