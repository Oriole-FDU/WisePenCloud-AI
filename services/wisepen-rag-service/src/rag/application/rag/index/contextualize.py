"""为 RetrievalChunk 生成可复用的检索上下文。

实现的是 Contextual Retrieval 思路：在向量/BM25 索引前，给每个 chunk 注入一段
由 LLM 生成的“上下文文本”，描述该 chunk 在文档中的位置与主题，以提升召回精度。
"""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from typing import TYPE_CHECKING

from rag.domain.models.content import ReadingBlock
from rag.domain.models.retrieval import RetrievalChunk
from rag.domain.models.structure import DocumentStructure, StructureMode
from rag.domain.repositories.mongo.generation_artifact_store import (
    GenerationArtifactStore,
)
from rag.utils.xml_markup import xml_cdata

if TYPE_CHECKING:
    from rag.utils.llm_clients import QueryClient


# Prompt 与响应 schema 的版本号；任一变更都会让旧 artifact key 失效，强制重新生成。
_PROMPT_VERSION = "contextual-indexing:v1"
_RESPONSE_SCHEMA_VERSION = "contextual-text:v1"
# 单次并发调用 LLM 的上限，避免大批量 chunk 同时打爆模型 API。
_MAX_CONCURRENCY = 5

_SYSTEM_PROMPT = """\
You create one short retrieval context for a target passage from a private document.
The context is prepended to the target passage for dense and BM25 retrieval; it is
not an answer, a rewrite of the passage, or a general document summary.

Use the section path to name the document topic and the reading block to resolve
local references. Describe only what is supported by the supplied
target passage and reading block. Do not add outside knowledge, recommendations, or
facts that are only implied by the section title. Keep the target passage's primary
language and stay concise.

Return only a JSON object with this shape:
{"contextual_text": "..."}
"""


class ContextualTextIndexer:
    """为结构化文档的检索块生成上下文并增强 `index_text`。"""

    __slots__ = ("_artifact_store", "_client")

    def __init__(
        self,
        *,
        client: QueryClient,
        artifact_store: GenerationArtifactStore,
    ) -> None:
        self._client = client
        self._artifact_store = artifact_store

    async def contextualize(
        self,
        *,
        resource_id: str,
        structure: DocumentStructure,
        reading_blocks: list[ReadingBlock],
        chunks: list[RetrievalChunk],
    ) -> list[RetrievalChunk]:
        """生成上下文并返回增强后的 chunk；flat_text/empty 不调用模型。"""
        # FLAT_TEXT/EMPTY 缺乏章节上下文，生成意义不大，直接透传。
        if not chunks or structure.mode in (StructureMode.FLAT_TEXT, StructureMode.EMPTY):
            return list(chunks)

        blocks_by_id = {block.block_id: block for block in reading_blocks}
        chunk_contexts: dict[str, RetrievalChunk] = {}
        for chunk in chunks:
            # chunk 的 section/reading_block 归属由 constructor 流水线保证。
            chunk_contexts[
                self._artifact_key(
                    chunk,
                    blocks_by_id[chunk.reading_block_id],
                )
            ] = chunk

        # 批量读取已持久化的上下文。
        stored_contexts = {
            key: value.strip()
            for key, value in (
                await self._artifact_store.get_many(
                    resource_id=resource_id,
                    artifact_kind="context",
                    artifact_keys=list(chunk_contexts),
                )
            ).items()
            if value.strip()
        }
        missing = [key for key in chunk_contexts if key not in stored_contexts]
        if missing:
            semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
            generated = await asyncio.gather(
                *(
                    self._generate(
                        chunk=chunk_contexts[key],
                        reading_block=blocks_by_id[chunk_contexts[key].reading_block_id],
                        semaphore=semaphore,
                    )
                    for key in missing
                )
            )
            # missing 与 generated 顺序一致，可严格 zip 成 dict。
            generated_by_key = dict(zip(missing, generated, strict=True))
            stored_contexts.update(generated_by_key)
            # 新生成的上下文写回缓存，供后续重复索引复用。
            await self._artifact_store.set_many(
                resource_id=resource_id,
                artifact_kind="context",
                artifacts=generated_by_key,
            )

        # 用上下文重写每个 chunk 的 index_text。
        contextualized_chunks: list[RetrievalChunk] = []
        for chunk in chunks:
            key = self._artifact_key(
                chunk,
                blocks_by_id[chunk.reading_block_id],
            )
            contextualized_chunks.append(
                chunk.with_contextual_text(stored_contexts[key])
            )
        return contextualized_chunks

    async def _generate(
        self,
        *,
        chunk: RetrievalChunk,
        reading_block: ReadingBlock,
        semaphore: asyncio.Semaphore,
    ) -> str:
        """调用 LLM 生成上下文文本。"""
        async with semaphore:
            response = await self._client.aquery(
                self._build_prompt(chunk, reading_block),
                system_prompt=_SYSTEM_PROMPT,
                max_tokens=256,
                response_format={"type": "json_object"},
            )

        payload = json.loads(response.content)
        if not isinstance(payload, dict):
            raise TypeError("contextual text response is not a JSON object")
        contextual_text = payload.get("contextual_text")
        if not isinstance(contextual_text, str) or not contextual_text.strip():
            raise ValueError("contextual_text is missing")
        return contextual_text.strip()

    def _artifact_key(
        self,
        chunk: RetrievalChunk,
        reading_block: ReadingBlock,
    ) -> str:
        """计算 chunk 的上下文缓存键。

        将所有可能影响生成结果的输入维度（prompt 版本、schema 版本、模型与 thinking
        配置、章节路径、reading block、chunk 文本）拼接后哈希，
        任何一项变化都会让 key 改变，从而强制重新生成。
        """
        input_fingerprint = "\0".join(
            (
                _PROMPT_VERSION,
                _RESPONSE_SCHEMA_VERSION,
                self._client.model,
                self._client.thinking or "default",
                "\n".join(chunk.section_path),
                reading_block.raw_text,
                chunk.raw_text,
                chunk.index_text,
            )
        )
        return sha256(input_fingerprint.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_prompt(
        chunk: RetrievalChunk,
        reading_block: ReadingBlock,
    ) -> str:
        return "\n".join(
            (
                "<contextual_indexing_request>",
                (
                    "<task>Create one concise context for the target retrieval chunk. "
                    "State what it is about and how it fits the section. "
                    "Do not answer a question.</task>"
                ),
                f"<section_path>{xml_cdata(' > '.join(chunk.section_path) or '(document root)')}</section_path>",
                f"<reading_block>{xml_cdata(reading_block.raw_text)}</reading_block>",
                f"<target_retrieval_chunk>{xml_cdata(chunk.raw_text)}</target_retrieval_chunk>",
                "</contextual_indexing_request>",
            )
        )
