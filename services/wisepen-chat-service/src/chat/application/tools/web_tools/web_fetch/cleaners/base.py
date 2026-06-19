from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CleanedOutput:
    """cleaner 层输出：清洗后的结构化结果。

    只承载下游 service 与 quality 判断所需字段：
    - markdown: 清洗后的正文 Markdown（核心输出）
    - title: 页面标题（可能为 None）
    - cleaner: 清洗器名称（trafilatura | crawl4ai）
    """

    markdown: str | None
    cleaner: str
    title: str | None = None


class BaseCleaner(Protocol):
    """cleaner 协议。

    cleaner 只负责"清洗 HTML"，输入 raw_html，输出 CleanedOutput。
    不负责抓取，不负责质量判断。
    """

    @property
    def name(self) -> str:
        """清洗器名称，用于 CleanedOutput.cleaner 字段。"""
        ...

    def clean(self, raw_html: str, *, url: str | None = None) -> CleanedOutput:
        """清洗 HTML，返回结构化结果。"""
        ...
