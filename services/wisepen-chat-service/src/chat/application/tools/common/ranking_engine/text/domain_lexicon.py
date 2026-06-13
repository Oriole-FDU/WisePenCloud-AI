from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DomainTerm:
    """领域词条，用于注入 RankingTokenizer 的 jieba 实例。"""

    text: str  # 领域词文本
    frequency: int | None = None  # 可选词频，传给 jieba
    tag: str | None = None  # 可选词性或类别
    source: str = "unknown"  # 词条来源，便于后续区分不同外部词表
    weight: float = 1.0  # 词权重，当前只保存，不参与 tokenizer 算分


@dataclass(frozen=True, slots=True)
class DomainLexicon:
    """领域词典，只负责保存、清洗、合并和注入领域词。"""

    terms: tuple[DomainTerm, ...] = ()  # 领域词列表

    @classmethod
    def from_terms(cls, terms: Iterable[DomainTerm]) -> DomainLexicon:
        """从领域词迭代器构造词典。"""
        return cls(terms=tuple(terms))

    def normalized_terms(self) -> tuple[DomainTerm, ...]:
        """返回过滤、去重后的领域词。"""
        result: list[DomainTerm] = []
        seen: set[str] = set()

        for term in self.terms:
            text = unicodedata.normalize("NFKC", term.text.strip())
            if not text or _is_punctuation_only(text) or text in seen:
                continue
            seen.add(text)
            result.append(
                DomainTerm(
                    text=text,
                    frequency=term.frequency,
                    tag=term.tag,
                    source=term.source,
                    weight=term.weight,
                )
            )

        return tuple(result)

    def words(self) -> tuple[str, ...]:
        """返回过滤后的领域词文本。"""
        return tuple(term.text for term in self.normalized_terms())

    def merge(self, other: DomainLexicon) -> DomainLexicon:
        """合并两个词典，并按词文本去重。"""
        return DomainLexicon(terms=(*self.terms, *other.terms)).normalized()

    def normalized(self) -> DomainLexicon:
        """返回归一化后的新词典。"""
        return DomainLexicon(terms=self.normalized_terms())

    def apply_to_jieba(self, tokenizer) -> None:
        """把领域词注入传入的 jieba.Tokenizer 实例。"""
        for term in self.normalized_terms():
            tokenizer.add_word(term.text, freq=term.frequency, tag=term.tag)


def _is_punctuation_only(text: str) -> bool:
    """判断文本是否只包含标点、符号或空白。"""
    for char in text:
        if char.isspace():
            continue
        category = unicodedata.category(char)
        if not category.startswith(("P", "S")):  # P 是标点符号，S 是特殊符号
            return False
    return True
