from abc import ABC, abstractmethod
from typing import List, Optional

from chat.domain.entities import ChatMessage


class ContextCompactor(ABC):
    """上下文压缩
    把最老的历史明细并入摘要，热上下文只保留低水位内的近期明细
    """

    @abstractmethod
    async def auto_compact(
        self,
        *,
        session_id: str,
        history: List[ChatMessage],
        existing_summary: Optional[str],
        context_tokens: int,
        context_window_tokens: int,
        high_watermark_ratio: Optional[float] = None,
        low_watermark_ratio: Optional[float] = None,
        summary_prompt: Optional[str] = None,
    ) -> tuple[List[ChatMessage], Optional[str]]:
        """达到高水位压缩，返回 (keep, new_summary)，未达水位原样返回 (history, existing_summary)"""
        pass

    @abstractmethod
    async def compact(
        self,
        *,
        session_id: str,
        history: List[ChatMessage],
        existing_summary: Optional[str],
        summary_prompt: Optional[str] = None,
    ) -> tuple[List[ChatMessage], Optional[str]]:
        """无视水位立即压缩：全部历史并入摘要，热上下文清空，返回 ([], new_summary)"""
        pass
