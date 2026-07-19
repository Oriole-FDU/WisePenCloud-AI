from __future__ import annotations

import thulac

from .base import RankingTokenizer


class ThuLacRankingTokenizer(RankingTokenizer):
    """使用 THULAC 分词的中文 tokenizer。"""

    __slots__ = ("_thulac_tokenizer",)

    def __init__(
            self,
            stopwords: frozenset[str] | None = None,
    ) -> None:
        super().__init__(stopwords=stopwords)
        # seg_only=True 表示只分词不标注词性，提高吞吐
        self._thulac_tokenizer = thulac.thulac(seg_only=True)

    def _tokenize_cjk(self, text: str) -> tuple[str, ...]:
        # THULAC 返回 "词_词性 词_词性 ..." 格式，这里只取词干
        segmented = self._thulac_tokenizer.cut(text, text=True)
        if not isinstance(segmented, str):
            return ()
        tokens: list[str] = []
        for part in segmented.split():
            value = part.split("_", 1)[0].strip()
            if value:
                tokens.append(value)
        return tuple(tokens)
