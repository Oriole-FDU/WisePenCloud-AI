from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

import litellm

from chat.core.config.app_settings import settings

EmbeddingInput = str | Sequence[str] | Sequence[int] | Sequence[Sequence[int]]


@dataclass(frozen=True)
class EmbeddingResult:
    embeddings: list[list[float]]
    raw: Any
    usage_tokens: int = 0


class LiteLLMEmbeddingClient:
    """通用 LiteLLM embedding client，不绑定项目配置。"""

    def __init__(
        self,
        model: str,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float | int | None = None,
        dimensions: int | None = None,
        encoding_format: str | None = "float",  # None 表示不传给 litellm
        **default_kwargs: Any,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self.dimensions = dimensions
        self.encoding_format = encoding_format
        self.default_kwargs = default_kwargs

    async def aembed(
        self,
        input: EmbeddingInput,
        *,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float | int | None = None,
        dimensions: int | None = None,
        encoding_format: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        response = await litellm.aembedding(
            **self._build_kwargs(
                model=model,
                input=input,
                api_base=api_base,
                api_key=api_key,
                timeout=timeout,
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
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float | int | None = None,
        dimensions: int | None = None,
        encoding_format: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        response = litellm.embedding(**self._build_kwargs(
            model=model, input=input, api_base=api_base, api_key=api_key,
            timeout=timeout, dimensions=dimensions, encoding_format=encoding_format,
            extra_kwargs=kwargs,
        ))
        return self._parse_response(response)

    def _build_kwargs(
        self,
        *,
        model: str | None,
        input: EmbeddingInput,
        api_base: str | None,
        api_key: str | None,
        timeout: float | int | None,
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
        # 调用级参数优先，回落到实例默认值；两者均为 None 则不传给 litellm
        call_level = {
            "api_base": api_base,
            "api_key": api_key,
            "timeout": timeout,
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
        data = _get_value(response, "data", []) or []
        embeddings = [list(_get_value(item, "embedding", [])) for item in data]
        usage = _get_value(response, "usage")
        usage_tokens = int(_get_value(usage, "total_tokens", 0) or 0) if usage is not None else 0
        return EmbeddingResult(embeddings=embeddings, raw=response, usage_tokens=usage_tokens)


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    """统一兼容 dict 和 object 的属性读取。"""
    return source.get(key, default) if isinstance(source, dict) else getattr(source, key, default)


@lru_cache(maxsize=1)
def build_embedding_client() -> LiteLLMEmbeddingClient:
    return LiteLLMEmbeddingClient(
        model=settings.EMBEDDING_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        dimensions=settings.EMBEDDING_DIMENSIONS,
    )
