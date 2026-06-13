from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Chunk, ChunkLevel, TextUnit


@dataclass(frozen=True, slots=True)
class BlockAwarePackerConfig:
    """块感知聚合器配置。"""

    chunk_size: int  # 单个 chunk 的目标字符数，超过则切分到下一个 chunk
    level: ChunkLevel = ChunkLevel.READ  # 输出 chunk 的用途层级
    separator: str = "\n\n"  # chunk 内多个 unit 文本之间的连接符
    chunk_id_prefix: str = "chunk"  # chunk ID 前缀（会被 ChunkFinalizer 覆盖）


class BlockAwarePacker:
    """块感知聚合器，将相邻 TextUnit 聚合成 Chunk。

    核心原则：不从 unit 中间切开，保证每个 unit 完整。
    当累计字符数超过 chunk_size 时，从当前 unit 前切分，
    已累积的 unit 组成一个 chunk，当前 unit 开始新的 chunk。

    适用于 Markdown 场景：MarkdownBlockSplitter 产出的小粒度 unit
    （标题、段落等）需要合并到合适大小才能作为检索单元。
    """

    __slots__ = ("config", "name")

    def __init__(self, config: BlockAwarePackerConfig) -> None:
        self.config = config
        self.name = "block_aware_packer"

    def pack(
        self,
        *,
        units: tuple[TextUnit, ...],
    ) -> tuple[Chunk, ...]:
        """将 unit 列表按 chunk_size 聚合成 chunk 列表。"""
        if not units:
            return ()

        chunks: list[Chunk] = []
        chunk_start = 0  # 当前 chunk 起始 unit 的 index
        chunk_chars = 0  # 当前 chunk 累计字符数
        chunk_size = self.config.chunk_size

        for unit in units:
            unit_chars = len(unit.text)
            # 如果加入当前 unit 会超限，且当前 chunk 不为空，则切分
            if unit.unit_index > chunk_start and chunk_chars + unit_chars > chunk_size:
                chunks.append(self._build_chunk(units, chunk_start, unit.unit_index - 1, len(chunks)))
                chunk_start = unit.unit_index
                chunk_chars = 0
            chunk_chars += unit_chars

        # 处理最后一个 chunk
        if chunk_start < len(units):
            chunks.append(self._build_chunk(units, chunk_start, len(units) - 1, len(chunks)))

        return tuple(chunks)

    def _build_chunk(
        self,
        units: tuple[TextUnit, ...],
        start_unit: int,
        end_unit: int,
        chunk_index: int,
    ) -> Chunk:
        """从 units[start_unit..end_unit] 构建一个 Chunk。"""
        selected = units[start_unit:end_unit + 1]
        # 用 separator 连接所有 unit 文本
        text = self.config.separator.join(unit.text for unit in selected if unit.text).strip()
        # 收集元信息
        unit_types = tuple(unit.unit_type for unit in selected)
        section_paths = tuple(
            path
            for path in dict.fromkeys(unit.section_path for unit in selected if unit.section_path)
        )
        titles = tuple(
            str(title)
            for title in (
                unit.metadata.get("title")
                for unit in selected
                if unit.metadata.get("title")
            )
        )

        return Chunk(
            chunk_id=f"{self.config.chunk_id_prefix}-{chunk_index}",
            text=text,
            chunk_index=chunk_index,
            level=self.config.level,
            start_offset=selected[0].start_offset,
            end_offset=selected[-1].end_offset,
            start_unit=selected[0].unit_index,
            end_unit=selected[-1].unit_index,
            metadata={
                "unit_types": unit_types,
                "block_types": unit_types,
                "start_block": selected[0].unit_index,
                "end_block": selected[-1].unit_index,
                "section_paths": section_paths,
                **({"titles": titles} if titles else {}),
            },
        )
