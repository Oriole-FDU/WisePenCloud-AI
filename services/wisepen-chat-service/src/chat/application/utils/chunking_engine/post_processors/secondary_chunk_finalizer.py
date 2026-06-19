from __future__ import annotations

from dataclasses import replace

from .utils import assign_chunk_ids, merge_heading_only, merge_short_tails
from ..models import Chunk


class SecondaryChunkFinalizer:
    """二次分块终态器，对父 chunk 做合并并维护父子引用关系。

    与 SingleLayerFinalizer 的区别：
    - SingleLayerFinalizer 适用于单层分块，合并所有 chunk 不区分层级。
    - SecondaryChunkFinalizer 适用于二次分块（父子嵌套），分离父子后只对父 chunk
      做合并，子 chunk 由 RecursiveTextSplitter 精切，不参与合并。

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

    __slots__ = ("name", "id_prefix", "min_size")

    def __init__(self, *, id_prefix: str = "", min_size: int = 320) -> None:
        self.name = "secondary_chunk_finalizer"
        self.id_prefix = id_prefix
        self.min_size = min_size

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        parents = tuple(c for c in chunks if c.parent_chunk_id is None)
        children = tuple(c for c in chunks if c.parent_chunk_id is not None)

        # 1. 对父 chunk 做合并，收集 remapped_ids
        result = merge_heading_only(parents)
        result = merge_short_tails(result.chunks, min_size=self.min_size)

        # 2. 用 remapped_ids 更新子 chunk 的 parent_chunk_id
        #    （被合并的父 ID → 存活的父 ID）
        if result.remapped_ids:
            children = tuple(
                replace(
                    child,
                    parent_chunk_id=result.remapped_ids.get(
                        child.parent_chunk_id, child.parent_chunk_id
                    ),
                )
                if child.parent_chunk_id is not None
                else child
                for child in children
            )

        # 3. 统一 ID 生成 + parent_chunk_id 最终重写
        return assign_chunk_ids((*result.chunks, *children), id_prefix=self.id_prefix)
