"""OpenAI embedding 异步公共客户端。"""

from __future__ import annotations

from openai import AsyncOpenAI


class EmbeddingClient:
    """绑定模型与输出维度，返回按输入顺序排列的向量。"""

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        dimensions: int | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        if dimensions is not None and dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._client = client
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int | None:
        """固定输出维度，供向量消费方核对响应；None 表示使用供应商默认值。"""
        return self._dimensions

    async def embed(
        self,
        *,
        texts: list[str],
    ) -> list[list[float]]:
        """异步生成文本向量。

        Args:
            texts: 待向量化的文本列表，不能为空。

        Returns:
            与输入顺序严格对应的向量列表。

        Raises:
            ValueError: 当 texts 为空，或返回结果数量与输入不一致时。
        """
        if not texts:
            raise ValueError("embedding texts must not be empty")

        kwargs: dict[str, object] = {
            "model": self._model,
            "input": texts,
        }
        if self._dimensions is not None:
            kwargs["dimensions"] = self._dimensions

        response = await self._client.embeddings.create(**kwargs)

        # 依据 item.index 排序，严格保障返回向量顺序与输入文本完全对应
        sorted_data = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in sorted_data]

        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match texts")

        return vectors
