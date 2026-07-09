from __future__ import annotations

from dataclasses import replace

from .models import Chunk, ChunkDocument, ChunkingResult, ChunkRole
from .pipeline import ChunkingPipeline


class ChunkingEngine:
    """通用分块引擎，按构造时绑定的 pipeline 执行分块流程。

    用法：
        engine = get_chunking_engine("markdown")
        result = engine.chunk(document=doc)
    """

    __slots__ = ("_pipeline",)

    def __init__(self, *, pipeline: ChunkingPipeline) -> None:
        self._pipeline = pipeline

    def chunk(
            self,
            *,
            document: ChunkDocument,
    ) -> ChunkingResult:
        """执行一次分块，返回分块结果。

        流程：block 切分 → chunk 聚合 → chunk 派生 → chunk 规范化 → 定位
        """
        pipeline = self._pipeline

        # 1. 切分：将文档拆成 TextBlock 列表
        blocks = pipeline.block_splitter.split(document=document)

        # 2. 聚合：将 TextBlock 聚合成 Chunk
        if pipeline.block_packer is not None:
            # 有聚合器时，按 block_packer 逻辑聚合（如 SizeBoundedBlockPacker 按大小合并相邻 block）
            chunks = pipeline.block_packer.pack(blocks=blocks)
        else:
            # 无聚合器时，每个 block 一对一映射为 chunk（适用于 RecursiveTextBlockSplitter 等已按目标大小切分的场景）
            chunks = tuple(
                Chunk(
                    chunk_id=f"chunk-{i}",
                    text=block.text,
                    chunk_index=i,
                    role=ChunkRole.FLAT,
                    start_offset=block.start_offset,
                    end_offset=block.end_offset,
                    start_block=block.block_index,
                    end_block=block.block_index,
                )
                for i, block in enumerate(blocks)
            )
        # 聚合后重新分配 chunk_index
        chunks = self._assign_chunk_indices(chunks)

        # 3. chunk 派生：从已有 chunk 生成关联 chunk，每步后重新分配 chunk_index
        for deriver in pipeline.chunk_derivers:
            chunks = deriver.process(chunks=chunks)
            chunks = self._assign_chunk_indices(chunks)

        # 4. chunk 规范化：按顺序执行所有规范化器，每步后重新分配 chunk_index
        for normalizer in pipeline.chunk_normalizers:
            chunks = normalizer.process(chunks=chunks)
            chunks = self._assign_chunk_indices(chunks)

        # 5. 定位：基于最终 chunk 构建语义定位信息
        locators = (
            pipeline.chunk_locator.index(
                document=document,
                blocks=blocks,
                chunks=chunks,
            )
            if pipeline.chunk_locator is not None
            else ()
        )

        return ChunkingResult(
            chunks=chunks,
            blocks=blocks,
            locators=locators,
            pipeline=pipeline.name,
            metadata={
                "block_count": len(blocks),
                "chunk_count": len(chunks),
                "locator_count": len(locators),
            },
        )

    @staticmethod
    def _assign_chunk_indices(chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """全局连续重新分配 chunk_index。

        chunk 派生器或规范化器可能增删 chunk（如 ChildChunkDeriver 追加子 chunk），
        导致 chunk_index 不连续。此方法全局从 0 重新编号，
        确保父子 chunk 不会出现 chunk_index 冲突。
        """
        return tuple(
            replace(chunk, chunk_index=i)
            for i, chunk in enumerate(chunks)
        )
