from __future__ import annotations

from dataclasses import dataclass

import unicodedata

from ._utils import (
    has_common_separator,
    make_cjk_bigrams,
    match_token_pattern,
    split_common_compound,
)
from ..stopwords import DEFAULT_STOPWORDS


@dataclass(frozen=True, slots=True)
class RankingTokenizerConfig:
    """Ranking Engine 词法分词配置。"""

    normalize_unicode: bool = True  # NFKC 归一化（全角转半角等）
    lowercase_latin: bool = True  # 拉丁 token 统一小写
    remove_stopwords: bool = True  # 过滤停用词
    deduplicate: bool = False  # BM25 通常保留重复词频
    enable_cjk_bigram: bool = True  # 中文 bigram 兜底
    split_common_separators: bool = True  # 按常见分隔符拆复合词
    keep_compound_token: bool = True  # 拆复合词时保留原 token
    min_token_length: int = 1  # 最短 token 长度
    max_tokens: int | None = None  # 防止 token 爆炸


class RankingTokenizer:
    """面向 BM25 / lexical ranking 的 tokenizer 基类。
    子类通过实现 _tokenize_cjk 接入不同中文分词器。
    """

    __slots__ = ("config", "stopwords")

    def __init__(
            self,
            config: RankingTokenizerConfig | None = None,
            stopwords: frozenset[str] | None = None,
    ) -> None:
        self.config = config or RankingTokenizerConfig()
        # 默认使用内置通用停用词表；调用方可传入自定义停用词覆盖
        self.stopwords = DEFAULT_STOPWORDS if stopwords is None else stopwords

    def tokenize(self, text: str) -> tuple[str, ...]:
        """将文本切分为面向排序的 token 序列。"""
        text = text.strip()
        if not text:
            return ()

        # 第一步：Unicode 归一化，统一全角/半角等异体字符
        if self.config.normalize_unicode:
            text = unicodedata.normalize("NFKC", text)

        cfg = self.config
        tokens: list[str] = []

        # 第二步：按正则三模式逐一提取原始 token
        for match in match_token_pattern(text):
            value = match.group(0)
            if match.lastgroup == "cjk":
                # 中文片段：使用子类分词器 + bigram 兜底
                tokens.extend(self._tokenize_cjk(value))
                if cfg.enable_cjk_bigram:
                    tokens.extend(make_cjk_bigrams(value))
                continue

            # 英数 / 复合词：按配置决定是否拆分
            if not cfg.split_common_separators or not has_common_separator(value):
                tokens.append(value)
                continue

            # 复合词拆分，例如 "GPT-4" → "GPT-4"(原型) + "GPT" + "4"
            if cfg.keep_compound_token:
                tokens.append(value)
            tokens.extend(split_common_compound(value))

        # 第三步：后处理（小写化、长度过滤、停用词过滤）
        result: list[str] = []
        for token in tokens:
            token = token.strip()
            # 拉丁字母统一转小写，保证大小写不敏感匹配
            if cfg.lowercase_latin and any(char.isascii() and char.isalpha() for char in token):
                token = token.casefold()
            if len(token) < cfg.min_token_length:
                continue
            if cfg.remove_stopwords and token in self.stopwords:
                continue
            result.append(token)

        # 第四步：可选去重与截断
        if cfg.deduplicate:
            result = list(dict.fromkeys(result))
        if cfg.max_tokens is not None:
            result = result[: max(0, cfg.max_tokens)]

        return tuple(result)

    def _tokenize_cjk(self, text: str) -> tuple[str, ...]:
        """模板方法：子类实现具体的中文分词逻辑。"""
        raise NotImplementedError
