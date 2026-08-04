from __future__ import annotations

from threading import Lock

import jieba
import thulac

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


class ThuLacRankingTokenizer(RankingTokenizer):
    """使用 THULAC 分词的中文 tokenizer。"""

    __slots__ = ("_lock", "_thulac_tokenizer")

    def __init__(
            self,
            stopwords: frozenset[str] | None = None,
    ) -> None:
        super().__init__(stopwords=stopwords)
        # seg_only=True 表示只分词不标注词性，提高吞吐
        self._thulac_tokenizer = thulac.thulac(seg_only=True)
        self._lock = Lock()

    def _tokenize_cjk(self, text: str) -> tuple[str, ...]:
        # THULAC 返回 "词_词性 词_词性 ..." 格式，这里只取词干
        # RankingPipeline 会在线程池中执行同步打分，共享 tokenizer 需要串行访问。
        with self._lock:
            segmented = self._thulac_tokenizer.cut(text, text=True)
        if not isinstance(segmented, str):
            return ()
        tokens: list[str] = []
        for part in segmented.split():
            value = part.split("_", 1)[0].strip()
            if value:
                tokens.append(value)
        return tuple(tokens)
