from __future__ import annotations

import re
from dataclasses import dataclass

import jieba
import unicodedata

from .domain_lexicon import DomainLexicon
from .stopwords import DEFAULT_STOPWORDS

# 混合 Token 正则：compound（带分隔符复合词）| alnum（纯英数）| cjk（连续汉字）
_TOKEN_PATTERN = re.compile(
    r"(?P<compound>[A-Za-z0-9]+(?:[._\-/][A-Za-z0-9]+)+)"
    r"|(?P<alnum>[A-Za-z0-9]+)"
    r"|(?P<cjk>[\u4e00-\u9fff]+)"
)

# 复合词内部分隔符（点、下划线、横线、斜杠）
_COMMON_SEPARATOR_PATTERN = re.compile(r"[._\-/]+")


@dataclass(frozen=True, slots=True)
class RankingTokenizerConfig:
    """Ranking Engine 词法分词配置。"""

    normalize_unicode: bool = True      # NFKC 归一化（全角→半角等）
    lowercase_latin: bool = True        # 拉丁 token casefold
    remove_stopwords: bool = True
    deduplicate: bool = False           # BM25 默认 False
    enable_cjk_segmentation: bool = True
    enable_cjk_bigram: bool = True
    split_common_separators: bool = True
    keep_compound_token: bool = True    # 同时保留复合 token 原形
    min_token_length: int = 1
    max_tokens: int | None = None       # 防止 token 爆炸


class RankingTokenizer:
    """面向 BM25 / lexical ranking 的窄义 tokenizer。"""

    __slots__ = ("config", "stopwords", "domain_lexicon", "_jieba_tokenizer")

    def __init__(
        self,
        config: RankingTokenizerConfig | None = None,
        stopwords: frozenset[str] | None = None,
        domain_lexicon: DomainLexicon | None = None,
    ) -> None:
        self.config = config or RankingTokenizerConfig()
        self.stopwords = DEFAULT_STOPWORDS if stopwords is None else stopwords
        self.domain_lexicon = domain_lexicon or DomainLexicon()
        tokenizer = jieba.Tokenizer()
        self.domain_lexicon.apply_to_jieba(tokenizer)
        self._jieba_tokenizer = tokenizer


    def tokenize(self, text: str) -> tuple[str, ...]:
        """把文本切成面向排序的 token。"""
        text = text.strip()
        if not text:
            return ()
        if self.config.normalize_unicode:
            text = unicodedata.normalize("NFKC", text)

        cfg = self.config
        domain_words = self.domain_lexicon.words()
        tokens: list[str] = []

        for match in _TOKEN_PATTERN.finditer(text):
            value = match.group(0)
            if match.lastgroup == "cjk":
                for word in domain_words:   # 保护领域词
                    if word in value:
                        tokens.append(word)
                if cfg.enable_cjk_segmentation:
                    tokens.extend(self._jieba_tokenizer.cut_for_search(value))
                if cfg.enable_cjk_bigram:
                    tokens.extend(_make_cjk_bigrams(value))
            else:
                if not cfg.split_common_separators or not _COMMON_SEPARATOR_PATTERN.search(value):
                    tokens.append(value)
                else:
                    if cfg.keep_compound_token:
                        tokens.append(value)
                    tokens.extend(_split_common_compound(value))

        # 归一化 + 过滤
        result: list[str] = []
        for token in tokens:
            token = token.strip()
            if cfg.lowercase_latin and any(c.isascii() and c.isalpha() for c in token):
                token = token.casefold()
            if len(token) < cfg.min_token_length:
                continue
            if cfg.remove_stopwords and token in self.stopwords:
                continue
            result.append(token)

        if cfg.deduplicate:
            result = list(dict.fromkeys(result))
        if cfg.max_tokens is not None:
            result = result[: max(0, cfg.max_tokens)]

        return tuple(result)


def _make_cjk_bigrams(text: str) -> tuple[str, ...]:
    """为连续中文片段生成 bigram。"""
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    if len(chars) < 2:
        return ()
    return tuple(chars[i] + chars[i + 1] for i in range(len(chars) - 1))


def _split_common_compound(token: str) -> tuple[str, ...]:
    """按通用分隔符拆分复合 token。"""
    return tuple(part for part in _COMMON_SEPARATOR_PATTERN.split(token) if part)
