"""通用 OpenAI-compatible SDK 的最小边界封装。"""

from openai import AsyncOpenAI


class ChatClient:
    """只负责一次 Chat Completions 请求与文本响应边界校验。

    业务提示词、结构化响应解析和请求节流属于调用用例，不能由这个
    SDK 边界猜测或统一处理。
    """

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """请求一次文本补全，并拒绝第三方返回的空内容。"""
        request: dict[str, object] = {"model": model, "messages": messages}
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if response_format is not None:
            request["response_format"] = response_format

        response = await self._client.chat.completions.create(**request)
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("chat completion response is empty")
        return content


class EmbeddingClient:
    """只负责一次 Embeddings 请求与向量响应边界校验。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        dimensions: int,
    ) -> list[list[float]]:
        """按调用方给定的文本列表请求向量，保持其顺序不变。"""
        if not texts:
            raise ValueError("embedding texts must not be empty")

        response = await self._client.embeddings.create(
            model=model,
            input=texts,
            dimensions=dimensions,
        )
        vectors = [list(item.embedding) for item in response.data]
        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match texts")
        if any(len(vector) != dimensions for vector in vectors):
            raise ValueError("embedding response dimensions do not match settings")
        return vectors
