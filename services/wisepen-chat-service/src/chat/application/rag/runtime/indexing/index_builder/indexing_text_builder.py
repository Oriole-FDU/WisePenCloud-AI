from typing import Dict, List

from chat.application.rag.runtime.models import (
    IndexingTextPair,
    SearchChunkContext,
)
from chat.application.rag.runtime.models import RagResource
from chat.application.rag.runtime.models import SearchChunk


class IndexingTextBuildError(RuntimeError):
    """索引文本构建失败。"""


class RagIndexingTextBuilder:
    """RAG 双索引文本构建器。

    - 将原始文本和 LLM 生成的上下文加工组合。
    - 为混合检索（向量检索 + 传统文本检索）分别提供定制的输入。
    """

    @staticmethod
    def build_text_pairs(
            *,
        resource: RagResource,
        search_chunks: List[SearchChunk],
        contexts: List[SearchChunkContext],
    ) -> Dict[str, IndexingTextPair]:
        """构建 SearchChunk 双索引文本。"""
        context_map = {context.chunk_id: context for context in contexts}
        sibling_windows = _build_sibling_windows(search_chunks)
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
                f"{search_chunk.text}\n"
                "\n"
                "Local parent context:\n"
                f"{sibling_windows.get(search_chunk.chunk_id, search_chunk.text)}"
            )


            text_pairs[search_chunk.chunk_id] = IndexingTextPair(
                semantic_indexing_text=semantic_indexing_text,
                keyword_text=search_chunk.text, # 关键词检索需要确保准确
            )

        return text_pairs


def _build_sibling_windows(search_chunks: List[SearchChunk]) -> Dict[str, str]:
    """为每个子块构造同父块内的一跳局部上下文窗口。"""
    grouped_chunks: Dict[str, List[SearchChunk]] = {}
    for chunk in search_chunks:
        grouped_chunks.setdefault(chunk.parent_chunk_id, []).append(chunk)

    windows: Dict[str, str] = {}
    for sibling_chunks in grouped_chunks.values():
        ordered_chunks = sorted(sibling_chunks, key=lambda chunk: chunk.chunk_index)
        for index, chunk in enumerate(ordered_chunks):
            start = max(0, index - 1)
            end = min(len(ordered_chunks), index + 2)
            windows[chunk.chunk_id] = "\n\n".join(
                sibling.text
                for sibling in ordered_chunks[start:end]
            )

    return windows
