
"""DashScope text rerank 的异步公共客户端。"""

from __future__ import annotations

from http import HTTPStatus

from dashscope import AioTextReRank


class RerankClient:
    """封装 DashScope 异步重排，返回文档索引与相关性分数。"""

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("rerank api key must not be empty")
        if not model.strip():
            raise ValueError("rerank model must not be empty")
        self.api_key = api_key
        self.model = model

    async def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        instruct: str | None = None,
        top_n: int | None = None,
    ) -> list[dict[str, int | float]]:
        """按相关性重排文档，返回原始索引与分数。

        示例：
            results = await rerank_client.rerank(
                query="什么是 RAG？",
                documents=[
                    "RAG 是检索增强生成技术。",
                    "Python 是一种编程语言。",
                ],
                top_n=2,
            )

            for result in results:
                print(result["index"], result["relevance_score"])
        """
        response = await AioTextReRank.call(
            model=self.model,
            query=query,
            documents=documents,
            instruct=instruct,
            top_n=top_n,
            api_key=self.api_key,
        )

        if response.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope rerank failed: "
                f"{response.code}: {response.message}"
            )

        return [
            {
                "index": result.index,
                "relevance_score": result.relevance_score,
            }
            for result in response.output.results
        ]
