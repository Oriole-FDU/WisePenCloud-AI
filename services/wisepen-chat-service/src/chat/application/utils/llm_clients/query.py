from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import litellm

from chat.core.config.app_settings import settings

ChatRole = Literal["system", "user", "assistant", "tool"]
Message = dict[str, Any]


@dataclass(frozen=True)
class QueryResult:
    content: str
    raw: Any
    usage_tokens: int = 0


class LiteLLMQueryClient:
    """通用小模型查询 client，prompt 由调用方显式传入。"""

    def __init__(
        self,
        model: str,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        temperature: float | None = 0.0,
        timeout: float | int | None = None,
        max_tokens: int | None = None,
        **default_kwargs: Any,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.default_kwargs = default_kwargs

    async def aquery(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        messages: list[Message] | None = None,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        timeout: float | int | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> QueryResult:
        request_kwargs = self._build_kwargs(
            prompt=prompt,
            system_prompt=system_prompt,
            messages=messages,
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_tokens,
            extra_kwargs=kwargs,
        )
        response = await litellm.acompletion(**request_kwargs)
        return self._parse_response(response)

    def query(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        messages: list[Message] | None = None,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        timeout: float | int | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> QueryResult:
        request_kwargs = self._build_kwargs(
            prompt=prompt,
            system_prompt=system_prompt,
            messages=messages,
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_tokens,
            extra_kwargs=kwargs,
        )
        response = litellm.completion(**request_kwargs)
        return self._parse_response(response)

    def _build_kwargs(
        self,
        *,
        prompt: str,
        system_prompt: str | None,
        messages: list[Message] | None,
        model: str | None,
        api_base: str | None,
        api_key: str | None,
        temperature: float | None,
        timeout: float | int | None,
        max_tokens: int | None,
        extra_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        request_messages = list(messages or [])
        if system_prompt:
            request_messages.insert(0, {"role": "system", "content": system_prompt})
        request_messages.append({"role": "user", "content": prompt})

        request_kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": request_messages,
            **self.default_kwargs,
            **extra_kwargs,
        }

        optional_values = {
            "api_base": api_base if api_base is not None else self.api_base,
            "api_key": api_key if api_key is not None else self.api_key,
            "temperature": (
                temperature if temperature is not None else self.temperature
            ),
            "timeout": timeout if timeout is not None else self.timeout,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        request_kwargs.update(
            {key: value for key, value in optional_values.items() if value is not None}
        )
        return request_kwargs

    @staticmethod
    def _parse_response(response: Any) -> QueryResult:
        choice = _get_value(response, "choices", [])[0]
        message = _get_value(choice, "message", {}) or {}
        content = _get_value(message, "content", "") or ""

        usage = _get_value(response, "usage")
        usage_tokens = 0
        if usage is not None:
            usage_tokens = int(_get_value(usage, "total_tokens", 0) or 0)

        return QueryResult(content=content, raw=response, usage_tokens=usage_tokens)


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


query_client = LiteLLMQueryClient(
    model=settings.QUERY_MODEL,
    api_base=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
)
