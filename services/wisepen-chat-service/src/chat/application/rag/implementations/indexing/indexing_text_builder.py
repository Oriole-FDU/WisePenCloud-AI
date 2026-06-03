from typing import Dict, List

from chat.application.rag.domain.index_chunks import (
    IndexingTextPair,
    SearchChunkContext,
)
from chat.application.rag.domain.index_chunks import SearchChunk
from chat.application.rag.domain.resource_lifecycle import RagResource


class IndexingTextBuildError(RuntimeError):
    """索引文本构建失败。"""


class RagIndexingTextBuilder:
    """RAG 双索引文本构建器。

    - 将原始文本和 LLM 生成的上下文加工组合。
    - 为混合检索（向量检索 + 传统文本检索）分别提供定制的输入。
    """
    
    def build_text_pairs(
        self,
        *,
        resource: RagResource,
        search_chunks: List[SearchChunk],
        contexts: List[SearchChunkContext],
    ) -> Dict[str, IndexingTextPair]:
        """构建 SearchChunk 双索引文本。"""
        context_map = {context.chunk_id: context for context in contexts}
        text_pairs: Dict[str, IndexingTextPair] = {}

        for search_chunk in search_chunks:
            context = context_map.get(search_chunk.chunk_id)
            if context is None:
                raise IndexingTextBuildError(
                    f"Context not found for search chunk: {search_chunk.chunk_id}"
                )

            semantic_indexing_text = (
                f"Resource: {resource.display_name}\n"
                f"Resource kind: {resource.resource_kind.value}\n"
                "\n"
                "Retrieval context:\n"
                f"{context.context_text}\n"
                "\n"
                "Chunk text:\n"
                f"{search_chunk.text}"
            )


            text_pairs[search_chunk.chunk_id] = IndexingTextPair(
                semantic_indexing_text=semantic_indexing_text,
                keyword_text=search_chunk.text, # 关键词检索需要确保准确
            )

        return text_pairs
