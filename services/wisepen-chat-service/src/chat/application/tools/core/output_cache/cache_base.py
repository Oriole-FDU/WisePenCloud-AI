from __future__ import annotations

from typing import Any

from chat.application.tools.core.output_cache.cache_store import ToolContentStore
from chat.core.config.app_settings import settings
from common.logger import warn

_Container = dict[str, Any] | list[Any]  # 可被原地改写的父节点
_Slot = str | int  # dict 的键 / list 的下标


class _BaseOutputCache:
    def __init__(self, *, tool_content_store: ToolContentStore) -> None:
        self._per_char_budget = settings.tools.content.preview_per_char_budget  # 单段限制
        self._total_char_budget = settings.tools.content.preview_total_char_budget  # 总限制
        if self._per_char_budget < 1:
            raise ValueError("per_char_budget must be greater than 0")
        if self._total_char_budget < 1:
            raise ValueError("total_char_budget must be greater than 0")
        self._store = tool_content_store

    def _allocate_budgets(self, sizes: list[int]) -> tuple[int, ...]:
        """给每段文本分配 preview 预算
        每段最多拿 _per_char_budget；总和不超过 _total_char_budget 时全部放行
        超出时短文本优先全额满足，省下的额度回流给长文本平分，除不尽的余数补发给较短几项
        """
        wants = [min(size, self._per_char_budget) for size in sizes]

        if sum(wants) <= self._total_char_budget:
            return tuple(wants)  # 总需求未超限，全额放行

        # 总预算不够时，先按“每段想要多少预算”从小到大排序，短文本会优先拿到完整预览
        budgets = [0] * len(wants)
        remaining = self._total_char_budget
        by_want = sorted(range(len(wants)), key=wants.__getitem__)

        for rank, index in enumerate(by_want):
            unserved = len(by_want) - rank
            fair_share = remaining // unserved

            if wants[index] <= fair_share:
                # 当前段的“理想预算”仍然在公平份额以内，先完整满足它
                budgets[index] = wants[index]
                remaining -= wants[index]
                continue

            # 当前项已超过平均份额，其后更大的需求也无法全额满足
            # 余额平分，余数逐个加一
            extra = remaining % unserved
            for offset, pending_index in enumerate(by_want[rank:]):
                budgets[pending_index] = fair_share + (1 if offset < extra else 0)
            break
        return tuple(budgets)

    async def _store_text(
        self,
        text: str,
        *,
        session_id: str,
        is_chunk: bool,
    ) -> tuple[str, int] | None:
        """把全文写进 Store，返回 (content_id, total_length)
        Store 不接管或失败时返回 None
        """
        try:
            receipt = await self._store.put_tool_content(
                session_id=session_id, text=text, is_chunk=is_chunk
            )
        except Exception as exc:  # noqa: BLE001 - 缓存故障不得破坏工具主结果
            warn("tool output claim-check store failed.", e=exc)
            return None
        if receipt is None:  # Store 选择不接管（比如空白、超最大长度），一样跳过
            return None
        return receipt
