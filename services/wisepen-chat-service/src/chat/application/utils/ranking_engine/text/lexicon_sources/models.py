from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LexiconSourceConfig:
    """本地词表加载通用配置。

    Attributes:
        source: 词表来源标识，如 "thuocl:THUOCL_IT"，用于 DomainTerm 的 source 字段溯源
        min_frequency: 最低词频阈值，低于此值的词条被过滤掉；None 表示不过滤
        max_terms: 最大加载词条数，达到上限后停止读取；None 表示不限制
        default_tag: 词条默认标签（如词性），THUOCL 词表本身不含词性标注；None 表示不标注
    """

    source: str = "lexicon"
    min_frequency: int | None = None
    max_terms: int | None = None
    default_tag: str | None = None
