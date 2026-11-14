from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar

import bm25s
import jieba

from .base import Candidate, Retriever

PayloadT = TypeVar("PayloadT")


def tokenize(text: str) -> list[str]:
    """使用 BM25 默认的 Unicode 归一化和 Jieba 分词。"""
    text = unicodedata.normalize("NFKC", text).casefold()  # NFKC 归一化 + 小写
    return [
        word
        for word in jieba.lcut(text)
        if any(char.isalnum() for char in word)  # 过滤纯符号
    ]


class BM25Retriever(Retriever[PayloadT]):
    """对调用方提供的候选文本执行普通 BM25。"""

    name = "bm25"

    def __init__(
        self,
        *,
        text_getter: Callable[[PayloadT], str],
        candidates: list[Candidate[PayloadT]] | None = None,
        top_k: int = 30,
    ) -> None:
        super().__init__(top_k=top_k)
        self.text_getter = text_getter
        self.candidates = candidates if candidates is not None else []

    async def retrieve(self, query: str) -> list[Candidate[PayloadT]]:
        if not self.candidates:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:  # 查询为空则按原顺序返回
            return self.candidates[: self.top_k]

        corpus_tokens = [
            tokenize(self.text_getter(candidate.payload))
            for candidate in self.candidates
        ]
        if not any(corpus_tokens):  # 语料全为空
            return self.candidates[: self.top_k]

        retriever = bm25s.BM25()
        retriever.index(corpus_tokens, show_progress=False)  # 建索引

        documents, scores = retriever.retrieve(
            [query_tokens],
            k=min(self.top_k, len(self.candidates)),
            sorted=True,
            show_progress=False,
        )

        # 排名字段属于当前算法阶段，payload 保留原始业务身份与定位。
        return [
            replace(
                self.candidates[int(index)],
                rank=rank + 1,
                score=float(scores[0][rank]),
            )
            for rank, index in enumerate(documents[0])
        ]
