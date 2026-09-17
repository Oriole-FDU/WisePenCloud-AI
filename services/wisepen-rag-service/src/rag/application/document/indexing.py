"""为 staged DocChunk 生成增强产物、写入检索投影并发布。"""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace

from pydantic import BaseModel, field_validator

from rag.application.document.context import build_inline_document_context
from rag.application.document.models import ContentRevision, DocChunk, Document
from rag.application.plugins.core.registry import RagPluginRegistry
from rag.application.publication import DocumentPublication
from rag.domain.repositories.acl import ResourceAclRepository
from rag.domain.repositories.doc_chunks import DocChunkRepository
from rag.domain.repositories.document_vectors import DocumentVectorRepository
from rag.domain.repositories.documents import DocumentRepository
from rag.domain.repositories.index_state import ResourceIndexStateRepository
from rag.utils import ChatClient, EmbeddingClient

# --- 常量配置 ---

_EMBEDDING_BATCH_SIZE = 32
_CONTEXTUALIZATION_MAX_TOKENS = 128

_SYSTEM_PROMPT = """Generate a brief context that situates the target chunk within its document for better search retrieval.

Use the surrounding document context to understand what the target refers to and where it belongs.
Add only information that helps retrieve or understand the target and is not already obvious from the target itself.

Use the same language as the target and keep established technical names unchanged.
Keep the context to one short sentence.

Return JSON only:
{"retrieval_context": "short context"}"""


# --- 增强响应模型 ---

class _RetrievalContextResponse(BaseModel):
    """OpenAI JSON 响应的外部边界；只接收检索上下文。"""

    retrieval_context: str

    @field_validator("retrieval_context")
    @classmethod
    def _require_retrieval_context(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("retrieval_context must not be empty")
        return value


# --- 文档索引构建器 ---

class DocumentIndexBuilder:
    """完成一版 staged 文档的增强、双向量投影和最终发布。"""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        doc_chunks: DocChunkRepository,
        resource_acls: ResourceAclRepository,
        index_states: ResourceIndexStateRepository,
        publication: DocumentPublication,
        document_vectors: DocumentVectorRepository,
        chat_client: ChatClient,
        embedding_client: EmbeddingClient,
        query_model: str,
        embedding_model: str,
        embedding_dimensions: int,
        llm_semaphore: asyncio.Semaphore,
        embedding_semaphore: asyncio.Semaphore,
        plugin_registry: RagPluginRegistry | None = None,
        enhancement_enabled: bool = True,
    ) -> None:
        if embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be positive")
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._resource_acls = resource_acls
        self._index_states = index_states
        self._publication = publication
        self._document_vectors = document_vectors
        self._chat_client = chat_client
        self._embedding_client = embedding_client
        self._query_model = query_model
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._llm_semaphore = llm_semaphore
        self._embedding_semaphore = embedding_semaphore
        self._enhancement_enabled = enhancement_enabled
        self._plugin_registry = plugin_registry or RagPluginRegistry()

    async def build_and_publish(self, revision: ContentRevision) -> None:
        """只构建当前 staged revision；成功投影后才切换 active 指针。"""
        # 校验 staged 状态
        state = (await self._index_states.get_states([revision.resource_id])).get(
            revision.resource_id
        )
        if state is None or state.staged_content_revision != revision.content_revision:
            if (
                state is not None
                and state.applied_content_revision == revision.content_revision
            ):
                return  # 已发布，无需重复构建
            raise ValueError(
                f"content revision {revision.content_revision} is not staged"
            )

        # 加载文档、chunk 和 ACL
        document = await self._load_document(revision)
        chunks = await self._doc_chunks.get_revision_chunks(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
        )
        resource_acl = (
            await self._resource_acls.get_resource_acls([revision.resource_id])
        ).get(revision.resource_id)
        if resource_acl is None:
            raise ValueError(f"resource ACL for {revision.resource_id} is missing")

        # 1. 为每个 Chunk 生成检索上下文
        enhanced_chunks = await self._enhance(document, chunks)

        # 2. 生成稠密向量
        dense_vectors = await _embed_chunks(
            self._embedding_client,
            model=self._embedding_model,
            dimensions=self._embedding_dimensions,
            chunks=enhanced_chunks,
            semaphore=self._embedding_semaphore,
        )

        # 3. 写入向量检索投影
        await self._document_vectors.write_revision(
            chunks=enhanced_chunks,
            dense_vectors=dense_vectors,
            resource_acl=resource_acl,
            filter_values=(
                plugin.filter_values(document)
                if (plugin := self._plugin_registry.match_document(document.metadata))
                is not None
                else {}
            ),
        )

        # 4. 最终发布，切换 active 指针
        await self._publication.apply_revision(revision)

    async def _load_document(self, revision: ContentRevision) -> Document:
        documents = await self._documents.get_revisions(
            [(revision.resource_id, revision.content_revision)]
        )
        document = documents.get((revision.resource_id, revision.content_revision))
        if document is None:
            raise ValueError(
                f"document revision {revision.content_revision} is missing"
            )
        return document

    async def _enhance(
        self,
        document: Document,
        chunks: Sequence[DocChunk],
    ) -> list[DocChunk]:
        """对尚未增强的 chunk 调用 LLM，返回增强后的完整列表。"""
        if not self._enhancement_enabled:
            return list(chunks)

        # 只处理缺失增强的 chunk
        pending = [chunk for chunk in chunks if not chunk.retrieval_context.strip()]
        if not pending:
            return list(chunks)

        by_chunk_id = {chunk.chunk_id: chunk for chunk in chunks}
        # 并发调用 LLM，允许部分失败
        results = await asyncio.gather(
            *(
                _generate_retrieval_context(
                    self._chat_client,
                    model=self._query_model,
                    document=document,
                    chunks=chunks,
                    chunk=chunk,
                    semaphore=self._llm_semaphore,
                )
                for chunk in pending
            ),
            return_exceptions=True,
        )

        # 收集结果，保留第一个异常
        failure: Exception | None = None
        for chunk, result in zip(pending, results, strict=True):
            if isinstance(result, Exception):
                failure = failure or result
                continue
            by_chunk_id[chunk.chunk_id] = replace(
                chunk,
                retrieval_context=result.retrieval_context,
            )

        enhanced = [by_chunk_id[chunk.chunk_id] for chunk in chunks]
        # 部分成功也要写回，避免重复消耗已成功的调用
        if enhanced != list(chunks):
            await self._doc_chunks.save_revision(enhanced)
        if failure is not None:
            raise failure
        return enhanced


# --- 模块级辅助函数 ---

async def _generate_retrieval_context(
    chat_client: ChatClient,
    *,
    model: str,
    document: Document,
    chunks: Sequence[DocChunk],
    chunk: DocChunk,
    semaphore: asyncio.Semaphore,
) -> _RetrievalContextResponse:
    """使用 target 原位标记的文档上下文生成 chunk-specific 检索上下文。"""
    document_context = build_inline_document_context(document, chunks, chunk)
    async with semaphore:
        content = await chat_client.complete(
            model=model,
            max_tokens=_CONTEXTUALIZATION_MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": document_context},
            ],
        )
    return _RetrievalContextResponse.model_validate(json.loads(content))


async def _embed_chunks(
    embedding_client: EmbeddingClient,
    *,
    model: str,
    dimensions: int,
    chunks: Sequence[DocChunk],
    semaphore: asyncio.Semaphore,
) -> dict[str, list[float]]:
    """分批生成稠密向量，返回 chunk_id → vector 的映射。"""
    if not chunks:
        return {}
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(chunks), _EMBEDDING_BATCH_SIZE):
        batch = chunks[start : start + _EMBEDDING_BATCH_SIZE]
        # Embedding API 对单次 input 数量和总 token 有上限；分批后按顺序回填
        async with semaphore:
            batch_vectors = await embedding_client.embed(
                model=model,
                texts=[chunk.get_retrieval_text() for chunk in batch],
                dimensions=dimensions,
            )
        vectors.update(
            {
                chunk.chunk_id: vector
                for chunk, vector in zip(batch, batch_vectors, strict=True)
            }
        )
    return vectors
