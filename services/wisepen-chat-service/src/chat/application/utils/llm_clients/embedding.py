from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

from openai import AsyncOpenAI, OpenAI

from chat.core.config.app_settings import settings

EmbeddingInput = str | Sequence[str] | Sequence[int] | Sequence[Sequence[int]]


@dataclass(frozen=True)
class EmbeddingResult:
    """Embedding 调用结果，屏蔽底层 SDK 差异。"""

    embeddings: list[list[float]]
    raw: Any
    usage_tokens: int = 0


class EmbeddingClient:
    """统一 embedding 客户端，不绑定具体 SDK 类型。"""

    def __init__(
            self,
            model: str,
            *,
            api_base: str | None = None,
            api_key: str | None = None,
            timeout: float | int | None = None,
            dimensions: int | None = None,
            encoding_format: str | None = "float",
            **default_kwargs: Any,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.encoding_format = encoding_format
        self.default_kwargs = default_kwargs

        client_kwargs: dict[str, Any] = {}
        if api_base is not None:
            client_kwargs["base_url"] = api_base
        if api_key is not None:
            client_kwargs["api_key"] = api_key
        if timeout is not None:
            client_kwargs["timeout"] = timeout

        self._async_client = AsyncOpenAI(**client_kwargs)
        self._sync_client = OpenAI(**client_kwargs)

    async def aembed(
            self,
            input: EmbeddingInput,
            *,
            model: str | None = None,
            dimensions: int | None = None,
            encoding_format: str | None = None,
            **kwargs: Any,
    ) -> EmbeddingResult:
        response = await self._async_client.embeddings.create(
            **self._build_kwargs(
                model=model,
                input=input,
                dimensions=dimensions,
                encoding_format=encoding_format,
                extra_kwargs=kwargs,
            )
        )
        return self._parse_response(response)

    def embed(
            self,
            input: EmbeddingInput,
            *,
            model: str | None = None,
            dimensions: int | None = None,
            encoding_format: str | None = None,
            **kwargs: Any,
    ) -> EmbeddingResult:
        response = self._sync_client.embeddings.create(
            **self._build_kwargs(
                model=model,
                input=input,
                dimensions=dimensions,
                encoding_format=encoding_format,
                extra_kwargs=kwargs,
            )
        )
        return self._parse_response(response)

    def _build_kwargs(
            self,
            *,
            model: str | None,
            input: EmbeddingInput,
            dimensions: int | None,
            encoding_format: str | None,
            extra_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": model or self.model,
            "input": input,
            **self.default_kwargs,
            **extra_kwargs,
        }
        # 调用级参数优先，回落到实例默认值；两者均为 None 则不传给底层 SDK
        call_level = {
            "dimensions": dimensions,
            "encoding_format": encoding_format,
        }
        for key, call_val in call_level.items():
            effective = call_val if call_val is not None else getattr(self, key)
            if effective is not None:
                kw[key] = effective
        return kw

    @staticmethod
    def _parse_response(response: Any) -> EmbeddingResult:
        data = response.data or []
        embeddings = [list(item.embedding) for item in data]
        usage = response.usage
        usage_tokens = int(usage.total_tokens or 0) if usage is not None else 0
        return EmbeddingResult(embeddings=embeddings, raw=response, usage_tokens=usage_tokens)


@lru_cache(maxsize=1)
def build_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(
        model=settings.EMBEDDING_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        dimensions=settings.EMBEDDING_DIMENSIONS,
    )
