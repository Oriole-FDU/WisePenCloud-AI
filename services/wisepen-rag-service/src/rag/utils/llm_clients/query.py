from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from rag.core.config.app_settings import settings
from openai import AsyncOpenAI, OpenAI

Message = dict[str, Any]
ThinkingMode = Literal["disabled"]


@dataclass(frozen=True, slots=True)
class QueryResult:
    """工具性小模型调用结果，屏蔽底层 SDK 响应结构。"""

    content: str
    raw: Any
    usage_tokens: int = 0


class QueryClient:
    """面向工具性小模型任务的 OpenAI-compatible 查询客户端。"""

    __slots__ = ("model", "thinking", "_async_client", "_sync_client")

    def __init__(
            self,
            model: str,
            *,
            api_base: str,
            api_key: str,
            thinking: ThinkingMode | None = None,
    ) -> None:
        self.model = model
        self.thinking = thinking
        self._async_client = AsyncOpenAI(base_url=api_base, api_key=api_key)
        self._sync_client = OpenAI(base_url=api_base, api_key=api_key)

    async def aquery(
            self,
            prompt: str,
            *,
            system_prompt: str | None = None,
            messages: list[Message] | None = None,
            max_tokens: int | None = None,
            response_format: dict[str, Any] | None = None,
    ) -> QueryResult:
        response = await self._async_client.chat.completions.create(
            **self._build_completion_kwargs(
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens,
                response_format=response_format,
            ),
        )
        return self._parse_response(response)

    def query(
            self,
            prompt: str,
            *,
            system_prompt: str | None = None,
            messages: list[Message] | None = None,
            max_tokens: int | None = None,
            response_format: dict[str, Any] | None = None,
    ) -> QueryResult:
        """同步查询入口，用于不在事件循环内的少量工具调用。"""
        response = self._sync_client.chat.completions.create(
            **self._build_completion_kwargs(
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens,
                response_format=response_format,
            ),
        )
        return self._parse_response(response)

    def _build_completion_kwargs(
            self,
            *,
            prompt: str,
            system_prompt: str | None,
            messages: list[Message] | None,
            max_tokens: int | None,
            response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages,
            ),
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if self.thinking is not None:
            kwargs["extra_body"] = {"thinking": {"type": self.thinking}}
        return kwargs

    @staticmethod
    def _build_messages(
            *,
            prompt: str,
            system_prompt: str | None,
            messages: list[Message] | None,
    ) -> list[Message]:
        result: list[Message] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        result.extend(
            {
                "role": str(message.get("role") or "user"),
                "content": str(message.get("content") or ""),
            }
            for message in messages or ()
        )
        result.append({"role": "user", "content": prompt})
        return result

    @staticmethod
    def _parse_response(response: Any) -> QueryResult:
        choices = response.choices or []
        content = str(choices[0].message.content or "") if choices else ""
        usage_tokens = int(response.usage.total_tokens or 0) if response.usage else 0
        return QueryResult(content=content, raw=response, usage_tokens=usage_tokens)


@lru_cache
def build_query_client(
        *,
        thinking: ThinkingMode | None = None,
) -> QueryClient:
    return QueryClient(
        model=settings.QUERY_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        thinking=thinking,
    )
