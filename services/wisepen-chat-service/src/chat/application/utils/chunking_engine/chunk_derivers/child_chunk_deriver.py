from __future__ import annotations

from dataclasses import dataclass

from ..models import Chunk, ChunkDocument, ChunkRole
from ..block_splitters.recursive_text_block_splitter import RecursiveTextBlockSplitter, RecursiveTextBlockSplitterConfig


@dataclass(frozen=True, slots=True)
class ChildChunkDeriverConfig:
    """子 chunk 生成配置。"""

    child_chunk_size: int = 600  # 子 chunk 目标字符数
    child_overlap: int = 100  # 子 chunk 重叠字符数
    child_role: ChunkRole = ChunkRole.CHILD  # 子 chunk 结构角色


class ChildChunkDeriver:
    """将父 chunk 拆分为子 chunk 用于精准检索。

    原理：
    - 父 chunk（role=PARENT）保持不变，用于 RAG 上下文注入
    - 对每个父 chunk 的文本，用 Markdown 专用分隔符切分为更小的子 chunk
      （父 chunk 由 MarkdownBlockSplitter + SizeBoundedBlockPacker 产出，文本仍是 Markdown）
    - 子 chunk（role=CHILD）通过 parent_chunk_id 关联到父 chunk
    - 检索时命中子 chunk，再通过 parent_chunk_id 取回完整父 chunk 作为上下文

    注意：此生成器应放在 FlatChunkNormalizer / ParentChildChunkNormalizer 之前。
    此时父 chunk 的 chunk_id 仍是 block_packer 分配的临时 ID（如 "chunk-0"），
    子 chunk 的 parent_chunk_id 引用该临时 ID；随后 normalizer 统一为
    父子 chunk 生成最终 ID 和 content_hash，并基于 old_id → new_id 映射
    更新子 chunk 的 parent_chunk_id，保证父子关系正确。
    """

    __slots__ = ("name", "config", "_block_splitter")

    def __init__(self, config: ChildChunkDeriverConfig | None = None) -> None:
        self.config = config or ChildChunkDeriverConfig()
        self.name = "child_chunk_deriver"
        self._block_splitter = RecursiveTextBlockSplitter(
            RecursiveTextBlockSplitterConfig.for_markdown(
                chunk_size=self.config.child_chunk_size,
                chunk_overlap=self.config.child_overlap,
            )
        )

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """对每个父 chunk 生成子 chunk，返回父 + 子 chunk 合并列表。"""
        children: list[Chunk] = []

        for parent in chunks:
            # 跳过已有 parent_chunk_id 的 chunk（避免递归嵌套）
            if parent.parent_chunk_id is not None:
                continue

            doc = ChunkDocument(text=parent.text)
            blocks = self._block_splitter.split(document=doc)

            # 如果父 chunk 只拆出 1 个子 chunk，无需二次分块
            if len(blocks) <= 1:
                continue

            for i, block in enumerate(blocks):
                # 子 chunk 的 offset 相对于原文：父 offset + 子在父文本内的 offset
                child_start = (
                    parent.start_offset + block.start_offset
                    if parent.start_offset is not None and block.start_offset is not None
                    else None
                )
                child_end = (
                    parent.start_offset + block.end_offset
                    if parent.start_offset is not None and block.end_offset is not None
                    else None
                )

                children.append(
                    Chunk(
                        chunk_id=f"{parent.chunk_id}:child:{i}",  # 临时 ID，由 normalizer 覆盖
                        text=block.text,
                        chunk_index=0,  # 由 engine 重新分配
                        role=self.config.child_role,
                        parent_chunk_id=parent.chunk_id,
                        start_offset=child_start,
                        end_offset=child_end,
                        metadata={
                            "child_index": i,
                            "child_count": len(blocks),
                            **_page_metadata_from(parent),
                        },
                    )
                )

        return (*chunks, *children)


def _page_metadata_from(parent: Chunk) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if page_label := parent.metadata.get("page_label"):
        metadata["page_label"] = page_label
    return metadata
