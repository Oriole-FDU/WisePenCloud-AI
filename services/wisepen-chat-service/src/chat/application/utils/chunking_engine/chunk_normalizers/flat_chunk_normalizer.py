from __future__ import annotations

from .chunk_merge import assign_chunk_ids, merge_heading_only, merge_short_tails
from ..models import Chunk


class FlatChunkNormalizer:
    """单层 chunk 规范化器，按序执行三步修正：

    1. 纯标题合并 — 把只有 "Section: ..." 行的 chunk 并入相邻正文 chunk
    2. 短尾合并 — 把过短的 chunk（< min_size）并入前一个 chunk
    3. ID 生成 — 计算 content hash 并生成稳定的 chunk ID

    本处理器适用于单层分块场景（无父子关系）。
    父子分块场景请使用 ParentChildChunkNormalizer，它对父 chunk 做合并后
    通过 remapped_ids 更新子 chunk 的 parent_chunk_id，避免引用悬空。
    """

    __slots__ = ("name", "id_prefix", "min_size")

    def __init__(self, *, id_prefix: str = "", min_size: int = 320) -> None:
        self.name = "flat_chunk_normalizer"
        self.id_prefix = id_prefix
        self.min_size = min_size

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        result = merge_heading_only(chunks)
        result = merge_short_tails(result.chunks, min_size=self.min_size)
        return assign_chunk_ids(result.chunks, id_prefix=self.id_prefix)
