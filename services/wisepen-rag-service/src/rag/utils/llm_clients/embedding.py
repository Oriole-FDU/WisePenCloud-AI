from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAI

EmbeddingInput = str | Sequence[str] | Sequence[int] | Sequence[Sequence[int]]


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Embedding 调用结果，屏蔽底层 SDK 响应结构。"""

    embeddings: list[list[float]]
    raw: Any
    usage_tokens: int = 0


class EmbeddingClient:
    """面向 OpenAI-compatible embedding API 的同步/异步客户端。"""

    __slots__ = (
        "_async_client",
        "_sync_client",
        "dimensions",
        "model",
    )

    def __init__(
            self,
            model: str,
            *,
            api_base: str,
            api_key: str,
            dimensions: int,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._async_client = AsyncOpenAI(base_url=api_base, api_key=api_key)
        self._sync_client = OpenAI(base_url=api_base, api_key=api_key)

    async def aembed(
            self,
            input: EmbeddingInput,
    ) -> EmbeddingResult:
        response = await self._async_client.embeddings.create(
            model=self.model,
            input=input,
            dimensions=self.dimensions,
        )
        return self._parse_response(response)

    def embed(
            self,
            input: EmbeddingInput,
    ) -> EmbeddingResult:
        response = self._sync_client.embeddings.create(
            model=self.model,
            input=input,
            dimensions=self.dimensions,
        )
        return self._parse_response(response)

    async def close(self) -> None:
        await self._async_client.close()
        self._sync_client.close()

    @staticmethod
    def _parse_response(response: Any) -> EmbeddingResult:
        embeddings = [list(item.embedding) for item in response.data or []]
        usage = response.usage
        usage_tokens = int(usage.total_tokens or 0) if usage is not None else 0
        return EmbeddingResult(
            embeddings=embeddings, raw=response, usage_tokens=usage_tokens
        )


def build_embedding_client(
    *,
    model: str,
    api_base: str,
    api_key: str,
    dimensions: int,
) -> EmbeddingClient:
    return EmbeddingClient(
        model=model,
        api_base=api_base,
        api_key=api_key,
        dimensions=dimensions,
    )
