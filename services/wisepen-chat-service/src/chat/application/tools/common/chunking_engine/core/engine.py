from __future__ import annotations

from dataclasses import replace

from .models import Chunk, ChunkDocument, ChunkingResult, ChunkLevel
from .pipeline import ChunkingPipeline


class ChunkingEngine:
    """通用分块引擎，按传入的 pipeline 配置执行分块流程。

    用法：
        engine = ChunkingEngine()
        result = engine.chunk(document=doc, pipeline=MARKDOWN_PIPELINE)
    """

    __slots__ = ()

    def chunk(
        self,
        *,
        document: ChunkDocument,
        pipeline: ChunkingPipeline,
    ) -> ChunkingResult:
        """按 pipeline 执行一次分块，返回分块结果。

        流程：预处理 → 切分 → 聚合 → 后处理 → 索引
        """

        # 1. 预处理：按顺序执行所有预处理器，逐步转换文档
        for processor in pipeline.pre_processors:
            document = processor.process(document=document)

        # 2. 切分：将文档拆成 TextUnit 列表
        units = pipeline.splitter.split(document=document)

        # 3. 聚合：将 TextUnit 聚合成 Chunk
        if pipeline.packer is not None:
            # 有聚合器时，按 packer 逻辑聚合（如 BlockAwarePacker 按大小合并相邻 unit）
            chunks = pipeline.packer.pack(units=units)
        else:
            # 无聚合器时，每个 unit 一对一映射为 chunk（适用于 RecursiveTextSplitter 等已按目标大小切分的场景）
            chunks = tuple(
                Chunk(
                    chunk_id=f"chunk-{i}",
                    text=unit.text,
                    chunk_index=i,
                    level=ChunkLevel.READ,
                    start_offset=unit.start_offset,
                    end_offset=unit.end_offset,
                    start_unit=unit.unit_index,
                    end_unit=unit.unit_index,
                )
                for i, unit in enumerate(units)
            )
        # 聚合后重新分配 chunk_index（按 level 分组计数）
        chunks = self._assign_chunk_indices(chunks)

        # 4. 后处理：按顺序执行所有后处理器，每步后重新分配 chunk_index
        for processor in pipeline.post_processors:
            chunks = processor.process(chunks=chunks)
            chunks = self._assign_chunk_indices(chunks)

        # 5. 索引：基于最终 chunk 构建额外语义索引
        indexes = (
            pipeline.extra_indexer.index(
                document=document,
                units=units,
                chunks=chunks,
            )
            if pipeline.extra_indexer is not None
            else ()
        )

        return ChunkingResult(
            chunks=chunks,
            units=units,
            indexes=indexes,
            pipeline=pipeline.name,
            metadata={
                "unit_count": len(units),
                "chunk_count": len(chunks),
                "index_count": len(indexes),
            },
        )

    @staticmethod
    def _assign_chunk_indices(chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """按 level 重新分配连续 chunk_index。

        后处理器可能增删 chunk，导致 chunk_index 不连续，
        此方法按 level 分组重新从 0 编号。
        """
        counters: dict[str, int] = {}
        assigned: list[Chunk] = []

        for chunk in chunks:
            index = counters.get(chunk.level, 0)
            counters[chunk.level] = index + 1
            assigned.append(replace(chunk, chunk_index=index))

        return tuple(assigned)
