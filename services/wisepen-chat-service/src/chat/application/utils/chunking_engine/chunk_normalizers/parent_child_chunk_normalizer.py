from __future__ import annotations

from dataclasses import replace

from .chunk_merge import assign_chunk_ids, merge_heading_only, merge_short_tails
from ..models import Chunk


class ParentChildChunkNormalizer:
    """父子 chunk 规范化器，对父 chunk 做合并并维护父子引用关系。

    与 FlatChunkNormalizer 的区别：
    - FlatChunkNormalizer 适用于单层分块，合并所有 chunk 不区分父子关系。
    - ParentChildChunkNormalizer 适用于父子分块，分离父子后只对父 chunk
      做合并，子 chunk 由 RecursiveTextBlockSplitter 精切，不参与合并。

    合并后的引用维护：
    父 chunk 合并会导致被合并父 chunk 的 chunk_id 消失。此时指向它的子 chunk
    的 parent_chunk_id 会变成悬空引用。本处理器通过合并函数返回的 remapped_ids
    （"被合并的旧 ID → 存活的旧 ID"）更新子 chunk 的 parent_chunk_id，
    保证父子关系在合并后仍然正确。

    流程：
    1. 按 parent_chunk_id 分离父 chunk 和子 chunk
    2. 对父 chunk 执行 heading-only 合并和短尾合并，收集 remapped_ids
    3. 用 remapped_ids 更新子 chunk 的 parent_chunk_id
    4. 调用 assign_chunk_ids 统一生成最终 ID 和 content_hash，
       并基于 old_id → new_id 映射再次重写 parent_chunk_id
    """

    __slots__ = ("name", "id_prefix", "min_size", "respect_page_boundaries")

    def __init__(
        self,
        *,
        id_prefix: str = "",
        min_size: int = 320,
        respect_page_boundaries: bool = True,
    ) -> None:
        self.name = "parent_child_chunk_normalizer"
        self.id_prefix = id_prefix
        self.min_size = min_size
        self.respect_page_boundaries = respect_page_boundaries

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        parents = tuple(c for c in chunks if c.parent_chunk_id is None)
        children = tuple(c for c in chunks if c.parent_chunk_id is not None)

        # 1. 对父 chunk 做合并，收集 remapped_ids
        heading_result = merge_heading_only(
            parents,
            respect_page_boundaries=self.respect_page_boundaries,
        )
        tail_result = merge_short_tails(
            heading_result.chunks,
            min_size=self.min_size,
            respect_page_boundaries=self.respect_page_boundaries,
        )
        remapped_ids = _merge_remapped_ids(
            heading_result.remapped_ids,
            tail_result.remapped_ids,
        )

        # 2. 用 remapped_ids 更新子 chunk 的 parent_chunk_id
        #    （被合并的父 ID → 存活的父 ID）
        if remapped_ids:
            children = tuple(
                replace(
                    child,
                    parent_chunk_id=remapped_ids.get(
                        child.parent_chunk_id, child.parent_chunk_id
                    ),
                )
                if child.parent_chunk_id is not None
                else child
                for child in children
            )

        # 3. 统一 ID 生成 + parent_chunk_id 最终重写
        return assign_chunk_ids((*tail_result.chunks, *children), id_prefix=self.id_prefix)


def _merge_remapped_ids(
    first: dict[str, str],
    second: dict[str, str],
) -> dict[str, str]:
    """合并连续两轮 chunk 合并产生的旧 ID 映射。"""
    remapped: dict[str, str] = {}

    for old_id, target_id in first.items():
        remapped[old_id] = second.get(target_id, target_id)

    remapped.update(second)
    return remapped
