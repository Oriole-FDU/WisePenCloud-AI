"""跨来源的基础论文事实；附加来源信息使用约定的自由字典。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Any


class PaperSource(StrEnum):
    """内置来源名称；注册新适配器可以使用自己的来源字符串。"""

    ARXIV = "arxiv"
    OPENALEX = "openalex"
    HUGGINGFACE = "huggingface"
    S2 = "s2"


@dataclass
class Paper:
    """来源专有事实按来源名放入 metadata，不让 Paper 依赖供应商类型。"""

    arxiv_id: str | None
    doi: str | None
    title: str | None
    abstract: str | None
    authors: list[str]
    publication_date: date | None
    year: int | None
    url: str
    pdf_url: str | None
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def project(self) -> Paper:
        """制作紧凑出口副本；压缩作者列表并剔除 S2 内部观测字段。"""
        metadata = deepcopy(self.metadata)
        s2 = metadata.get("s2")
        if s2 is not None:
            s2.pop("observed_at", None)
            if s2.get("tldr") is not None:
                s2["tldr"].pop("model", None)
        compact_authors = [f"{self.authors[0]} et al."] if len(self.authors) > 1 else self.authors[:]
        return replace(self, authors=compact_authors, metadata=metadata)
