from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING

from chat.application.rag.repositories import RagContextIndexingCache
from chat.application.utils.xml_markup import xml_cdata
from .models import RagContentProjection, RagRetrievalChunk, RagSectionReadingBlock

if TYPE_CHECKING:
    from chat.application.utils.llm_clients import QueryClient

_PROMPT_VERSION = "context-indexing:v1"
_MAX_CONCURRENCY = 5

_SYSTEM_PROMPT = """\
You create one short retrieval context for a target passage from a private document.
The context is prepended to the target passage for dense and BM25 retrieval; it is
not an answer, a rewrite of the passage, or a general document summary.

Use the section path to name the document topic and the reading block to resolve
local references such as "this method" or "the next step". Describe only what is
supported by the supplied target passage and reading block. Do not add outside
knowledge, recommendations, or facts that are only implied by the section title.
Keep the target passage's primary language. For Chinese, stay within 120 Chinese
characters; for other languages, use a similarly concise length.

Return only a JSON object with this shape:
{"indexing_context": "..."}
"""


class ContextIndexingError(RuntimeError):
    """检索上下文生成失败，当前 Kafka 消息应重试。"""


class ContextIndexingService:
    """为 Chunk 生成用于检索增强的上下文描述。

    indexing_context 不参与正文事实扩展，只用于帮助检索阶段理解：
    - 当前 chunk 讨论的主题；
    - 当前内容在文档结构中的位置；
    - chunk 与局部上下文之间的关系。

    所有生成结果通过 content_hash + prompt_version 缓存，避免重复调用模型。
    """

    __slots__ = ("_cache", "_client")

    def __init__(self, *, client: QueryClient, cache: RagContextIndexingCache) -> None:
        self._client = client
        self._cache = cache

    async def contextualize(self, projection: RagContentProjection) -> RagContentProjection:
        if not projection.retrieval_chunks:
            return projection

        blocks_by_id = {
            block.block_id: block for block in projection.reading_blocks
        }
        cache_keys = tuple(
            self._cache_key(chunk, blocks_by_id[chunk.reading_block_id])
            for chunk in projection.retrieval_chunks
        )
        chunks_by_key = dict(
            zip(cache_keys, projection.retrieval_chunks, strict=True)
        )

        # 批量读取已有缓存，避免重复生成。
        contexts = {
            key: value.strip()
            for key, value in (await self._cache.get_many(tuple(chunks_by_key))).items()
            if value.strip()
        }
        missing = {key: chunk for key, chunk in chunks_by_key.items() if key not in contexts}

        if missing:
            semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
            generated = await asyncio.gather(
                *(
                    self._generate(
                        key,
                        chunk,
                        blocks_by_id[chunk.reading_block_id],
                        semaphore,
                    )
                    for key, chunk in missing.items()
                ),
                return_exceptions=True,
            )

            failures: list[BaseException] = []
            for result in generated:
                if isinstance(result, BaseException):
                    failures.append(result)
                    continue
                key, indexing_context = result
                contexts[key] = indexing_context

            # 只缓存成功结果，失败任务由上层 Kafka 重试。
            await self._cache.set_many({key: contexts[key] for key in missing if key in contexts})

            if failures:
                raise failures[0]

        contextualized_chunks = tuple(
            chunk.with_indexing_context(contexts[key])
            for key, chunk in zip(
                cache_keys,
                projection.retrieval_chunks,
                strict=True,
            )
        )
        summaries_by_section: dict[str, list[str]] = {}
        for key, chunk in zip(cache_keys, projection.retrieval_chunks, strict=True):
            summaries_by_section.setdefault(chunk.section_id, []).append(contexts[key])

        return replace(
            projection,
            sections=tuple(
                replace(
                    section,
                    summary=" ".join(
                        dict.fromkeys(summaries_by_section[section.section_id])
                    )[:500],
                )
                if section.section_id in summaries_by_section
                else section
                for section in projection.sections
            ),
            retrieval_chunks=contextualized_chunks,
        )

    async def _generate(
            self,
            key: str,
            chunk: RagRetrievalChunk,
            reading_block: RagSectionReadingBlock,
            semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        try:
            async with semaphore:
                response = await self._client.aquery(
                    "\n".join(
                        (
                            "<context_indexing_request>",
                            "<task>Create one concise context for the target "
                            "retrieval chunk. State what it is about and how it "
                            "fits the section. Do not answer a question.</task>",
                            "<input_definitions>",
                            "<section_path_definition>The document heading path. "
                            "Use it to identify the topic and scope.</section_path_definition>",
                            "<reading_block_definition>Verbatim text from the same "
                            "section. Use it only to resolve local references and "
                            "nearby meaning.</reading_block_definition>",
                            "<target_chunk_definition>Verbatim text that will be "
                            "retrieved. The generated context must describe this "
                            "text, not replace it.</target_chunk_definition>",
                            "</input_definitions>",
                            f"<section_path>{xml_cdata(' > '.join(chunk.section_path) or '(document root)')}</section_path>",
                            f"<section_reading_block>{xml_cdata(reading_block.raw_text)}</section_reading_block>",
                            f"<target_retrieval_chunk>{xml_cdata(chunk.raw_text)}</target_retrieval_chunk>",
                            "</context_indexing_request>",
                        )
                    ),
                    system_prompt=_SYSTEM_PROMPT,
                    max_tokens=256,
                    response_format={"type": "json_object"},
                )

            payload = json.loads(response.content)
            if not isinstance(payload, dict):
                raise ValueError("response is not a JSON object")

            indexing_context = payload.get("indexing_context")
            if not isinstance(indexing_context, str) or not indexing_context.strip():
                raise ValueError("indexing_context is missing")

            return key, indexing_context.strip()
        except Exception as error:
            raise ContextIndexingError(f"context indexing failed for chunk {chunk.chunk_id}") from error

    def _cache_key(
            self,
            chunk: RagRetrievalChunk,
            reading_block: RagSectionReadingBlock,
    ) -> str:
        """生成上下文缓存键。

        缓存依赖：
        - prompt 版本；
        - 模型版本；
        - 推理模式；
        - chunk 内容；
        - 输入上下文。

        任意影响生成结果的因素变化都会自然失效。
        """
        value = "\0".join(
            (
                _PROMPT_VERSION,
                self._client.model,
                self._client.thinking or "default",
                sha256(chunk.raw_text.encode("utf-8")).hexdigest(),
                sha256(reading_block.raw_text.encode("utf-8")).hexdigest(),
                sha256(chunk.index_text.encode("utf-8")).hexdigest(),
            )
        )
        return sha256(value.encode("utf-8")).hexdigest()
