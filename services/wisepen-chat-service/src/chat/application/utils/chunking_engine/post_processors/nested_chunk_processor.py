from __future__ import annotations

from dataclasses import dataclass

from ..models import Chunk, ChunkDocument, ChunkLevel
from ..splitters.recursive_text_splitter import RecursiveTextSplitter, RecursiveTextSplitterConfig


@dataclass(frozen=True, slots=True)
class NestedChunkConfig:
    """嵌套分块配置。"""

    child_chunk_size: int = 600  # 子 chunk 目标字符数
    child_overlap: int = 100  # 子 chunk 重叠字符数
    child_level: ChunkLevel = ChunkLevel.RETRIEVE  # 子 chunk 用途层级


class NestedChunkProcessor:
    """嵌套分块后处理器，将父 chunk 拆分为子 chunk 用于精准检索。

    原理：
    - 父 chunk（level=READ）保持不变，用于 RAG 上下文注入
    - 对每个父 chunk 的文本，用 RecursiveTextSplitter 切分为更小的子 chunk
    - 子 chunk（level=RETRIEVE）通过 parent_chunk_id 关联到父 chunk
    - 检索时命中子 chunk，再通过 parent_chunk_id 取回完整父 chunk 作为上下文

    注意：此处理器应放在 ChunkFinalizer 之前，
    因为 ChunkFinalizer 会生成最终 chunk_id，
    而子 chunk 需要引用父 chunk 的最终 ID。
    """

    __slots__ = ("name", "config", "_splitter")

    def __init__(self, config: NestedChunkConfig | None = None) -> None:
        self.config = config or NestedChunkConfig()
        self.name = "nested_chunk_processor"
        self._splitter = RecursiveTextSplitter(
            RecursiveTextSplitterConfig(
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
            units = self._splitter.split(document=doc)

            # 如果父 chunk 只拆出 1 个子 chunk，无需嵌套
            if len(units) <= 1:
                continue

            for i, unit in enumerate(units):
                # 子 chunk 的 offset 相对于原文：父 offset + 子在父文本内的 offset
                child_start = (
                    parent.start_offset + unit.start_offset
                    if parent.start_offset is not None and unit.start_offset is not None
                    else None
                )
                child_end = (
                    parent.start_offset + unit.end_offset
                    if parent.start_offset is not None and unit.end_offset is not None
                    else None
                )

                children.append(
                    Chunk(
                        chunk_id=f"child-{parent.chunk_id}-{i}",
                        text=unit.text,
                        chunk_index=0,  # 由 engine 重新分配
                        level=self.config.child_level,
                        parent_chunk_id=parent.chunk_id,
                        start_offset=child_start,
                        end_offset=child_end,
                        metadata={
                            "child_index": i,
                            "child_count": len(units),
                        },
                    )
                )

        return (*chunks, *children)
