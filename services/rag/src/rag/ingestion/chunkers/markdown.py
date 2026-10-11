"""将 Common Markdown 分块结果投影为 RAG 摄入 Chunk。"""

from common.utils.markdown import MarkdownChunker as CommonMarkdownChunker

from rag.core.schema import Document
from rag.ingestion.chunkers.base import Chunk


class MarkdownChunker:
    """复用 Common 的解析和分块算法，仅适配字段及资源内标识。"""

    def __init__(
        self,
        target_chunk_tokens: int = 800,
        split_threshold_tokens: int = 1200,
    ) -> None:
        self._chunker = CommonMarkdownChunker(
            target_chunk_tokens, split_threshold_tokens
        )

    async def split(self, document: Document) -> list[Chunk]:
        result = self._chunker.chunk(document.raw_markdown)
        # Common ID 只描述文档内部结构，RAG 用资源与 revision 限定其身份。
        identity_prefix = f"{document.resource_id}:{document.content_revision}"
        return [
            Chunk(
                chunk_id=f"{identity_prefix}:{chunk.chunk_id}",
                resource_id=document.resource_id,
                content_revision=document.content_revision,
                chunk_index=chunk.chunk_index,
                section_id=(
                    f"{identity_prefix}:{chunk.section_id}"
                    if chunk.section_id is not None
                    else None
                ),
                section_path=chunk.section_path,
                raw_text=chunk.text,
                content_token_count=chunk.content_token_count,
                anchor_labels=chunk.anchor_labels,
                # source_spans、node_ids 等内部事实留在 Common，不注入 metadata。
            )
            for chunk in result.chunks
        ]
