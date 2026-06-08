import hashlib
from dataclasses import dataclass
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter

from chat.application.rag.domain.index_chunks import (
    ChunkingResult,
    RetrieveChunk,
    SearchChunk,
)
from chat.application.rag.domain.resource_lifecycle import RagResource


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """RAG 分块配置。

    - retrieve_chunk 是阅读单位，粒度较大。
    - search_chunk 是检索单位，粒度较小。
    - search_chunk 只能在单个 retrieve_chunk 内继续切分，不能跨父块。
    """

    retrieve_chunk_size: int = 2400
    retrieve_chunk_overlap: int = 200
    search_chunk_size: int = 600
    search_chunk_overlap: int = 100


class RagChunker:
    """RAG 分块器。

    将资源原文按父子块两级分块：
    - retrieve_chunk（父块）：较大的阅读单位，用于最终的证据引用。
    - search_chunk（子块）：较小的检索单位，在单个父块内继续切分。
    """

    def __init__(self, config: ChunkingConfig) -> None:
        """初始化分块器。

        Args:
            config: 分块配置（父块/子块大小、重叠窗口）。
        """
        self._config = config
        self._retrieve_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.retrieve_chunk_size,
            chunk_overlap=config.retrieve_chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )
        self._search_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.search_chunk_size,
            chunk_overlap=config.search_chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )

    def build_chunks(self, resource: RagResource) -> ChunkingResult:
        """构建资源的父子二级分块。

        分块算法：
        1. 先将资源原文使用 retrieve_splitter 切成 retrieve_chunk（父块），
           以 (resource_kind:resource_id:retrieve:index:hash) 为 chunk_id。
        2. 再在每个 retrieve_chunk 内部使用 search_splitter 继续切分为 search_chunk（子块），
           以 (resource_kind:resource_id:search:parent_index:index:hash) 为 chunk_id。
        3. 子块不跨父块，通过 parent_chunk_id 关联。

        Args:
            resource: 待分块的资源。

        Returns:
            包含父块和子块列表的分块结果。
        """

        # 先将资源原文切成 retrieve_chunk，作为最终阅读和引用的父块。
        raw_retrieve_texts = self._retrieve_splitter.split_text(resource.content)
        retrieve_texts = [text.strip() for text in raw_retrieve_texts if text.strip()]

        retrieve_chunks: List[RetrieveChunk] = []
        for chunk_index, text in enumerate(retrieve_texts):
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = (
                f"{resource.resource_kind.value}:"
                f"{resource.resource_id}:"
                f"retrieve:{chunk_index}:"
                f"{content_hash[:16]}"
            )

            retrieve_chunks.append(
                RetrieveChunk(
                    chunk_id=chunk_id,
                    resource_id=resource.resource_id,
                    resource_kind=resource.resource_kind,
                    chunk_index=chunk_index,
                    text=text,
                    content_hash=content_hash,
                )
            )

        # 再在每个 retrieve_chunk 内部切 search_chunk，确保子块不跨父块。
        search_chunks: List[SearchChunk] = []
        for parent_chunk in retrieve_chunks:
            raw_search_texts = self._search_splitter.split_text(parent_chunk.text)
            search_texts = [text.strip() for text in raw_search_texts if text.strip()]

            for chunk_index, text in enumerate(search_texts):
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                chunk_id = (
                    f"{resource.resource_kind.value}:"
                    f"{resource.resource_id}:"
                    f"search:{parent_chunk.chunk_index}:{chunk_index}:"
                    f"{content_hash[:16]}"
                )

                search_chunks.append(
                    SearchChunk(
                        chunk_id=chunk_id,
                        parent_chunk_id=parent_chunk.chunk_id,
                        resource_id=resource.resource_id,
                        resource_kind=resource.resource_kind,
                        parent_chunk_index=parent_chunk.chunk_index,
                        chunk_index=chunk_index,
                        text=text,
                        content_hash=content_hash,
                    )
                )

        return ChunkingResult(
            retrieve_chunks=retrieve_chunks,
            search_chunks=search_chunks,
        )
