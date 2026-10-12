"""将 Common Markdown 分块结果投影为 RAG 摄入 Chunk。"""

from copy import deepcopy

from common.utils.markdown import MarkdownChunker as CommonMarkdownChunker

from rag.core.schema import Chunk, Document
from rag.core.schema.content import rag_section_id


class MarkdownChunker:
    """复用 Common 算法，仅适配字段与资源/revision 限定的内容身份。"""

    def __init__(
        self,
        target_chunk_tokens: int = 800,
        split_threshold_tokens: int = 1200,
    ) -> None:
        self._chunker = CommonMarkdownChunker(
            target_chunk_tokens, split_threshold_tokens
        )

    async def split(self, document: Document) -> list[Chunk]:
        result = self._chunker.chunk(document.text)
        # Chunk ID 保持原格式，章节 ID 则与 Document 的不透明 hash 身份一致。
        identity_prefix = f"{document.resource_id}:{document.content_revision}"
        return [
            Chunk(
                chunk_id=f"{identity_prefix}:{chunk.chunk_id}",
                resource_id=document.resource_id,
                content_revision=document.content_revision,
                chunk_index=chunk.chunk_index,
                section_id=(
                    rag_section_id(
                        resource_id=document.resource_id,
                        content_revision=document.content_revision,
                        common_section_id=chunk.section_id,
                    )
                    if chunk.section_id is not None
                    else None
                ),
                section_path=chunk.section_path,
                raw_text=chunk.text,
                content_token_count=chunk.content_token_count,
                anchor_labels=chunk.anchor_labels,
                # 业务字段逐块透传，嵌套值不能与 Document 或其他 Chunk 共享。
                metadata=deepcopy(document.metadata),
                # source_spans、node_ids 等内部事实留在 Common，不注入 metadata。
            )
            for chunk in result.chunks
        ]
