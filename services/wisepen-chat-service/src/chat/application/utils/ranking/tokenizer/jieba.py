from __future__ import annotations

import jieba

from .base import RankingTokenizer


class JiebaRankingTokenizer(RankingTokenizer):
    """使用 jieba 搜索模式分词的中文 tokenizer。"""

    __slots__ = ("_jieba_tokenizer",)

    def __init__(
        self,
        stopwords: frozenset[str] | None = None,
    ) -> None:
        super().__init__(stopwords=stopwords)
        # jieba.cut_for_search 在精确分词基础上补充粗粒度切分，
        # 适合搜索引擎场景，能覆盖更多候选边界
        self._jieba_tokenizer = jieba.Tokenizer()

    def _tokenize_cjk(self, text: str) -> tuple[str, ...]:
        return tuple(self._jieba_tokenizer.cut_for_search(text))
