from typing import List, Optional

from zeroentropy import APIError, AsyncZeroEntropy

from chat.application.rag.runtime.retrieval.stages.rerank_models import (
    RerankableDocument,
    RerankedDocument,
)


class RerankError(RuntimeError):
    """Rerank 调用失败。"""


class ZeroEntropyReranker:
    """ZeroEntropy 重排模型。"""

    def __init__(
            self,
            client: AsyncZeroEntropy,
            model: str,
    ) -> None:
        """初始化对象依赖。"""
        self._client = client
        self._model = model

    async def rerank(
        self,
        *,
        query: str,
        documents: List[RerankableDocument],
        top_n: Optional[int] = None,
    ) -> List[RerankedDocument]:
        """重排候选文档并返回按相关度降序排列的结果。"""

        if not documents:
            return []

        try:
            response = await self._client.models.rerank(
                model=self._model,
                query=query,
                documents=[doc.text for doc in documents],
                top_n=top_n,
            )
        except APIError as e:
            raise RerankError(f"ZeroEntropy rerank failed: {e}") from e

        reranked: List[RerankedDocument] = []
        for rank, item in enumerate(response.results):
            index = item.index
            score = item.relevance_score

            # 确保模型输出正确
            if index < 0 or index >= len(documents):
                raise RerankError("ZeroEntropy rerank result.index is out of range.")
            if not 0.0 <= score <= 1.0:
                raise RerankError("ZeroEntropy rerank relevance_score must be in [0, 1].")

            reranked.append(
                RerankedDocument(
                    id=documents[index].id,
                    score=score,
                    rank=rank,
                )
            )

        return reranked
