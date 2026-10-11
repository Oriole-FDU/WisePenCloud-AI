from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar

from common.utils.ai_clients import RerankClient

from ..retrievers.base import Candidate
from .base import Reranker

logger = logging.getLogger(__name__)


PayloadT = TypeVar("PayloadT")


class ModelReranker(Reranker[PayloadT]):
    """通过已配置模型客户端执行精排，失败时保留原始顺序。"""

    def __init__(
        self,
        *,
        client: RerankClient,
        text_getter: Callable[[PayloadT], str],
        top_n: int | None = None,
        instruct: str = "",
    ) -> None:
        self.client = client  # 精排模型客户端
        self.name = client.model
        self.text_getter = text_getter
        self.top_n = top_n  # 最多返回条数
        self.instruct = instruct  # 精排指令

    async def rerank(
        self,
        focus: str,
        candidates: list[Candidate[PayloadT]],
    ) -> list[Candidate[PayloadT]]:
        original = candidates
        if not original:
            return []  # 无候选直接返回空列表

        try:
            response = await self.client.rerank(
                query=focus,
                documents=[
                    self.text_getter(candidate.payload) for candidate in original
                ],
                instruct=self.instruct or None,
                top_n=self.top_n,
            )
            return _apply_response(original, response)
        except Exception as exc:
            logger.warning(
                "reranker failed; preserving retrieval order", exc_info=exc
            )  # 精排失败时回退原顺序
            return original


def _apply_response(
    candidates: list[Candidate[PayloadT]],
    response: list[dict[str, Any]],
) -> list[Candidate[PayloadT]]:
    selected: list[Candidate[PayloadT]] = []
    selected_indexes: set[int] = set()

    for item in response:
        index = item.get("index")
        score = item.get("relevance_score")

        # 校验索引是否合法
        if type(index) is not int or index < 0 or index >= len(candidates):
            raise ValueError("reranker result index is out of range")

        # 校验是否重复、分数类型是否合法
        if index in selected_indexes or not isinstance(score, (int, float)):
            raise ValueError("reranker result is duplicated or has an invalid score")

        score = float(score)

        # 分数必须位于 [0, 1]
        if not 0.0 <= score <= 1.0:
            raise ValueError("reranker score must be in [0, 1]")

        selected_indexes.add(index)
        source = candidates[index]
        selected.append(
            replace(
                source,
                rank=len(selected) + 1,  # 按精排顺序重设排名
                score=score,  # 写入精排分数
            )
        )

    # 未命中精排结果的候选，按原始顺序追加
    selected.extend(
        replace(source, rank=len(selected) + 1)
        for index, source in enumerate(candidates)
        if index not in selected_indexes
    )
    return selected
