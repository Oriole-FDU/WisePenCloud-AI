from __future__ import annotations

import re

import unicodedata

_TOKEN_PATTERN = re.compile(
    r"(?P<compound>[A-Za-z0-9]+(?:[._\-/][A-Za-z0-9]+)+)"
    r"|(?P<alnum>[A-Za-z0-9]+)"
    r"|(?P<cjk>[\u4e00-\u9fff]+)"
)
_COMMON_SEPARATOR_PATTERN = re.compile(r"[._\-/]+")
_DEFAULT_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "as",
        "at",
        "from",
        "的",
        "了",
        "和",
        "与",
        "及",
        "或",
        "是",
        "在",
        "对",
        "中",
        "为",
        "以",
        "于",
        "也",
        "而",
        "及其",
    }
)


class RankingTokenizer:
    """面向 BM25 / lexical ranking 的 tokenizer 基类。
    子类通过实现 _tokenize_cjk 接入不同中文分词器。
    """

    __slots__ = ("stopwords",)

    def __init__(
            self,
            stopwords: frozenset[str] | None = None,
    ) -> None:
        self.stopwords = _DEFAULT_STOPWORDS if stopwords is None else stopwords

    def tokenize(self, text: str) -> tuple[str, ...]:
        """将文本切分为面向排序的 token 序列。"""
        text = text.strip()
        if not text:
            return ()

        text = unicodedata.normalize("NFKC", text)

        tokens: list[str] = []

        # 中文交给具体分词器并补充 bigram；复合词同时保留原词和拆分结果。
        for match in _TOKEN_PATTERN.finditer(text):
            value = match.group(0)
            if match.lastgroup == "cjk":
                segmented_tokens = self._tokenize_cjk(value)
                tokens.extend(segmented_tokens)

                # 只过滤分词与补充 bigram 的跨来源重叠；分词结果和原文中的真实重复仍计入 TF。
                segmented_token_set = set(segmented_tokens)
                for index in range(len(value) - 1):
                    bigram = value[index] + value[index + 1]
                    if bigram not in segmented_token_set:
                        tokens.append(bigram)
                continue

            if not _COMMON_SEPARATOR_PATTERN.search(value):
                tokens.append(value)
                continue

            tokens.append(value)
            tokens.extend(
                part for part in _COMMON_SEPARATOR_PATTERN.split(value) if part
            )

        result: list[str] = []
        for token in tokens:
            token = token.strip().casefold()
            if token in self.stopwords:
                continue
            result.append(token)

        return tuple(result)

    def _tokenize_cjk(self, text: str) -> tuple[str, ...]:
        """模板方法：子类实现具体的中文分词逻辑。"""
        raise NotImplementedError
