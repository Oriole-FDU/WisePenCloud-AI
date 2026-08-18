"""QueryClient 与 Neo4j GraphRAG SDK 的适配。

GraphRAG SDK 提供了一套基于 LLMInterfaceV2 的抽取流水线，但项目自己的
QueryClient 接口与其不完全一致；本模块负责两层适配：

1. QueryClientGraphRagLLM：把 QueryClient 适配为 LLMInterfaceV2，
   让 SDK 的 LLMEntityRelationExtractor 能直接调用项目模型。
2. GraphRagCandidateExtractor：包装 SDK 的 LLMEntityRelationExtractor，
   把一组 KnowledgeExtractionWindow 渲染为 SDK 接受的 TextChunks 输入，
   并返回合并后的 Neo4jGraph（包含全部窗口的节点与关系）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_graphrag.experimental.components.entity_relation_extractor import (
    LLMEntityRelationExtractor,
    OnError,
)
from neo4j_graphrag.experimental.components.schema import GraphSchema
from neo4j_graphrag.experimental.components.types import (
    Neo4jGraph,
    TextChunk,
    TextChunks,
)
from neo4j_graphrag.llm.base import LLMInterfaceV2
from neo4j_graphrag.llm.types import LLMResponse
from neo4j_graphrag.types import LLMMessage
from pydantic import BaseModel

from .windows import KnowledgeExtractionWindow, render_extraction_window

if TYPE_CHECKING:
    from rag.utils.llm_clients.query import QueryClient


class QueryClientGraphRagLLM(LLMInterfaceV2):
    """将项目 QueryClient 适配为 GraphRAG 的结构化输出接口。

    实现 invoke / ainvoke 两个方法（同步 + 异步），供 SDK 内部按需调用；
    """

    supports_structured_output = True

    def __init__(self, *, client: QueryClient) -> None:
        super().__init__(model_name=client.model)
        self._client = client

    @property
    def artifact_profile(self) -> str:
        """返回 LLM 的生成画像标识，参与 artifact 缓存键计算。"""
        return f"{self._client.model}:{self._client.thinking or 'default'}"

    def invoke(
        self,
        input: list[LLMMessage],
        *,
        response_format: type[BaseModel] | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        # SDK 可能传入额外 kwargs，但项目 QueryClient 不接受，直接丢弃避免报错。
        del kwargs
        prompt, messages = _query_messages(input)
        result = self._client.query(
            prompt,
            messages=messages,
            response_format=_response_format(response_format),
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
            response_format=_response_format(response_format),
        )
        return LLMResponse(content=result.content)


class GraphRagCandidateExtractor:
    """调用 GraphRAG SDK，从一组业务窗口生成未经发布的候选图。"""

    __slots__ = ("_extractor",)

    def __init__(self, *, llm: LLMInterfaceV2, max_concurrency: int) -> None:
        self._extractor = LLMEntityRelationExtractor(
            llm=llm,  # type: ignore[arg-type]
            create_lexical_graph=False,     # 不生成额外的词汇图
            on_error=OnError.RAISE,     # 抽取出错时直接抛出，便于上层感知
            max_concurrency=max_concurrency,
            use_structured_output=True,     # 要求模型以 JSON schema 返回，便于解析。
        )

    async def extract(
        self,
        windows: list[KnowledgeExtractionWindow],
        schema: GraphSchema,
    ) -> Neo4jGraph:
        """渲染所有窗口并调用 SDK 抽取，返回包含全部窗口的合并候选图。
        """
        return await self._extractor.run(
            chunks=TextChunks(
                chunks=[
                    TextChunk(
                        uid=window.window_id,
                        index=window.ordinal,
                        text=render_extraction_window(window),
                        metadata={
                            "resource_id": window.resource_id,
                            "content_revision": window.content_revision,
                        },
                    )
                    for window in windows
                ]
            ),
            schema=schema,
        )


def _query_messages(input: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
    """把 SDK 的 LLMMessage 列表转换为 QueryClient 接受的 (prompt, messages)。 """
    if not input:
        raise ValueError("GraphRAG LLM input must contain at least one message")
    messages = [
        {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or ""),
        }
        for message in input
    ]
    # 弹出最后一条作为 prompt，剩余作为历史 messages。
    return str(messages.pop()["content"]), messages


def _response_format(
    value: type[BaseModel] | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """把 SDK 传入的 response_format 转换为 OpenAI 兼容的描述字典。"""
    if value is None or isinstance(value, dict):
        return value
    return {
        "type": "json_schema",
        "json_schema": {
            "name": value.__name__,
            "schema": value.model_json_schema(),
        },
    }
