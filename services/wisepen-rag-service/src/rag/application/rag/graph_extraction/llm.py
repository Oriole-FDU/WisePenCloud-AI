from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_graphrag.llm.base import LLMInterfaceV2
from neo4j_graphrag.llm.types import LLMResponse
from neo4j_graphrag.types import LLMMessage
from pydantic import BaseModel

if TYPE_CHECKING:
    from rag.utils.llm_clients.query import QueryClient


class QueryClientGraphRagLLM(LLMInterfaceV2):
    """将内部 QueryClient 适配为 Neo4j GraphRAG 所需的 LLM 接口。"""

    # GraphRAG SDK 通过此标志决定是否走结构化输出分支。
    supports_structured_output = True

    def __init__(self, *, client: QueryClient) -> None:
        super().__init__(model_name=client.model)
        self._client = client

    def invoke(
            self,
            input: list[LLMMessage],
            *,
            response_format: type[BaseModel] | dict[str, Any] | None = None,
            **kwargs: Any,
    ) -> LLMResponse:
        del kwargs
        prompt, messages = _query_messages(input)
        result = self._client.query(
            prompt,
            messages=messages,
            response_format=_query_response_format(response_format),
        )
        return LLMResponse(content=result.content)

    async def ainvoke(
            self,
            input: list[LLMMessage],
            *,
            response_format: type[BaseModel] | dict[str, Any] | None = None,
            **kwargs: Any,
    ) -> LLMResponse:
        del kwargs
        prompt, messages = _query_messages(input)
        result = await self._client.aquery(
            prompt,
            messages=messages,
            response_format=_query_response_format(response_format),
        )
        return LLMResponse(content=result.content)


def _query_messages(input: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
    """转换 GraphRAG 消息格式为内部 QueryClient 格式。"""
    if not input:
        raise ValueError("GraphRAG LLM input must contain at least one message")

    messages = [
        {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or ""),
        }
        for message in input
    ]

    # QueryClient 将最后一条消息作为当前 prompt，其余作为历史消息。
    prompt = str(messages.pop()["content"])
    return prompt, messages


def _query_response_format(
        response_format: type[BaseModel] | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """将 Pydantic Model 转换为 OpenAI-compatible JSON Schema 格式。"""
    if response_format is None or isinstance(response_format, dict):
        return response_format

    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_format.__name__,
            "schema": response_format.model_json_schema(),
        },
    }
