from __future__ import annotations

import json

from chat.application.tools.core.execution.result import CacheableText, ToolOutput
from chat.application.tools.core.llm.invocation import ToolInvocation
from dataclasses import asdict
from typing import Any

from chat.application.tools.core.output_cache.cache_store import ToolContentStore, ToolContentReceipt
from chat.core.config.app_settings import settings
from common.logger import warn

_TRUNCATION_MARKER = "\n...\n"


class ToolOutputCache:
    def __init__(self, *, tool_content_store: ToolContentStore) -> None:
        self._per_char_budget = settings.TOOL_CONTENT_PREVIEW_PER_CHAR_BUDGET # 单段限制
        self._total_char_budget = settings.TOOL_CONTENT_PREVIEW_TOTAL_CHAR_BUDGET # 总限制
        if self._per_char_budget < 1: raise ValueError("per_char_budget must be greater than 0")
        if self._total_char_budget < 1: raise ValueError("total_char_budget must be greater than 0")

        self._content_store = tool_content_store

    async def process(self, *, tool_output: ToolOutput, invocation: ToolInvocation, session_id: str) -> ToolOutput:
        # 挑出非空正文
        cacheable_texts = tuple(
            cacheable_text for cacheable_text in tool_output.cacheable_texts
            if cacheable_text.content and not cacheable_text.content.isspace()
        )

        if not cacheable_texts:
            return tool_output

        receipts: dict[int, ToolContentReceipt] = {}

        for index, cacheable_text in enumerate(cacheable_texts):
            try:
                result = await self._content_store.put(
                    session_id=session_id,
                    text=cacheable_text.content,
                    metadata=dict(cacheable_text.metadata),
                )
            except Exception as exc:
                # 缓存是工具输出的附加能力；写入失败时保留 preview，避免缓存故障
                # 扩散为整个工具调用失败，同时通过日志保留故障诊断信息。
                warn(
                    "tool output cache content store failed.",
                    e=exc,
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.tool_call_id,
                    cacheable_text_index=index,
                )
                continue

            if result is not None:
                receipts[index] = result

        preview_budget = self._preview_budget_calc(cacheable_texts)
        contents: list[dict[str, Any]] = []
        for index, cacheable_text in enumerate(cacheable_texts):
            preview, truncated = self._build_preview_text(cacheable_text.content, preview_budget[index])

            item: dict[str, Any] = {
                "content_index": index,
                "text": preview,
                "truncated": truncated,
                "total_length": len(cacheable_text.content),
                "metadata": dict(cacheable_text.metadata),
            }
            receipt = receipts.get(index)
            if receipt is not None:
                item.update(asdict(receipt))

            contents.append(item)

        try:
            payload = json.loads(tool_output.content)
        except json.JSONDecodeError:
            payload = {"text": tool_output.content}
        if not isinstance(payload, dict): # tool_output.content 是合法 JSON 但不是 object
            payload = {"output": payload}
        payload["contents"] = contents
        return ToolOutput(
            content=json.dumps(payload, ensure_ascii=False),
            images=tool_output.images,
        )

    def _preview_budget_calc(
        self,
        cacheable_texts: tuple[CacheableText, ...],
    ) -> tuple[int, ...]:
        """为每段 preview 分配字符预算，优先保留更短、更容易完整展示的内容"""

        desired = tuple(
            min(len(item.content), self._per_char_budget)
            for item in cacheable_texts
        )
        if sum(desired) <= self._total_char_budget:
            return desired

        # 总预算不够时，先按“每段想要多少预算”从小到大排序，短文本会优先拿到完整预览
        budgets = [0] * len(desired)
        remaining = self._total_char_budget
        ordered = sorted(range(len(desired)), key=desired.__getitem__)
        for position, index in enumerate(ordered):
            # 当前位置之后还剩多少段在等预算
            # 按剩余量做平均，避免前面分配太多导致后面的段直接变成 0
            pending = len(ordered) - position
            fair_share = remaining // pending
            if desired[index] <= fair_share:
                # 当前段的“理想预算”仍然在公平份额以内，先完整满足它
                budgets[index] = desired[index]
                remaining -= desired[index]
                continue
            # 从这一段开始，后面的每一段都只按同一份公平预算分配
            for pending_index in ordered[position:]:
                budgets[pending_index] = fair_share
            # 余数按原始排序顺序往前补，确保总和刚好等于 total_char_budget
            for pending_index in ordered[position : position + remaining % pending]:
                budgets[pending_index] += 1
            break
        return tuple(budgets)

    @staticmethod
    def _build_preview_text(text: str, char_budget: int) -> tuple[str, bool]:
        """按字符预算生成模型可见 preview"""

        if len(text) <= char_budget:
            return text, False
        if char_budget <= 0:
            return "", True
        if char_budget <= len(_TRUNCATION_MARKER):
            return text[:char_budget], True

        available = char_budget - len(_TRUNCATION_MARKER)
        head_budget = available - available // 2
        tail_budget = available // 2
        tail = text[-tail_budget:] if tail_budget else ""
        return text[:head_budget] + _TRUNCATION_MARKER + tail, True
