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
    """将 ToolReturn 中可缓存的大文本存储，并生成模型可见的内容预览。

    这层只处理输出治理，不决定工具业务语义。它做两件事：
    1. 把 `cacheable_texts` 逐段写入 `ToolContentStore`，换回稳定的 `content_id`；
    2. 按模型预算构造预览内容，并把 `content_id`、`total_length` 和其他
       读取凭证塞回返回 payload。
    """

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
        """把可缓存正文治理成 `contents` 结构，并保留工具原始可见结果。"""
        payload = dict(tool_return.visible_result)

        # 空白正文不进入治理链
        cacheable_texts = tuple(
            cacheable_text
            for cacheable_text in tool_return.cacheable_texts
            if cacheable_text.text and not cacheable_text.text.isspace()
        )
        if not cacheable_texts:
            return payload

        # 先入库再生成预览：content_id 是后续 session tools 的入口，而不是
        # 预览里的附属字段。把这一步放在前面，才能保证模型先拿到稳定凭证。
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
        """为每段 preview 分配 token 预算，优先保留更短、更容易完整展示的内容。"""

        desired = tuple(
            bounded_canonical_token_count(item.text, self._per_token_budget)
            for item in cacheable_texts
        )
        if sum(desired) <= self._total_token_budget:
            return desired

        # 总预算不够时，先按“每段想要多少预算”从小到大排序。
        # 这样短文本会优先拿到完整预览，长文本不会一上来就吞掉总预算。
        budgets = [0] * len(desired)
        remaining = self._total_token_budget
        # 这里保存的是原始下标顺序，后面还要把预算写回对应的 contents 项。
        ordered = sorted(range(len(desired)), key=desired.__getitem__)
        for position, index in enumerate(ordered):
            # 当前位置之后还剩多少段在等预算。按剩余量做平均，避免前面分配
            # 太多导致后面的段直接变成 0。
            pending = len(ordered) - position
            fair_share = remaining // pending
            if desired[index] <= fair_share:
                # 当前段的“理想预算”仍然在公平份额以内，先完整满足它。
                budgets[index] = desired[index]
                remaining -= desired[index]
                continue
            # 从这一段开始，后面的每一段都只按同一份公平预算分配。
            # 这样最终会形成一个平滑的截断边界，而不是只截断某一段。
            for pending_index in ordered[position:]:
                budgets[pending_index] = fair_share
            # 余数按原始排序顺序往前补，确保总和刚好等于 total_token_budget。
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
        """把一段正文渲染成最终 payload 项，并附加可追溯的入库回执。"""

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
                    # 这些字段来自入库回执，不是 preview 本身的派生值。
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
        """逐段存储大文本，并返回成功写入的内容回执。

        每段文本单独写入，避免一段失败拖垮同一工具返回中的其他段。
        返回值保留原始索引，方便后续把 receipt 重新挂回对应的 contents 项。
        """
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
