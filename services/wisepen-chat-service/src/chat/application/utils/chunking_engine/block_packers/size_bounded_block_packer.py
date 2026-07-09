from __future__ import annotations

from dataclasses import dataclass

from ..models import Chunk, ChunkRole, TextBlock, BlockKind


@dataclass(frozen=True, slots=True)
class SizeBoundedBlockPackerConfig:
    """按目标大小聚合 TextBlock 的配置。"""

    chunk_size: int  # 单个 chunk 的目标字符数，超过则切分到下一个 chunk
    role: ChunkRole = ChunkRole.FLAT  # 输出 chunk 的结构角色
    separator: str = "\n\n"  # chunk 内多个 block 文本之间的连接符
    chunk_id_prefix: str = "chunk"  # chunk ID 前缀（会被 normalizer 覆盖）
    hard_boundary_block_kinds: tuple[BlockKind, ...] = ()  # 这些 block 永远开启新 chunk


class SizeBoundedBlockPacker:
    """按目标大小将相邻 TextBlock 聚合成 Chunk。

    核心原则：不从 block 中间切开，保证每个 block 完整。
    当累计字符数超过 chunk_size 时，从当前 block 前切分，
    已累积的 block 组成一个 chunk，当前 block 开始新的 chunk。

    适用于 Markdown 场景：MarkdownBlockSplitter 产出的小粒度 block
    （标题、段落等）需要合并到合适大小才能作为检索单元。
    """

    __slots__ = ("config", "name")

    def __init__(self, config: SizeBoundedBlockPackerConfig) -> None:
        self.config = config
        self.name = "size_bounded_block_packer"

    def pack(
            self,
            *,
            blocks: tuple[TextBlock, ...],
    ) -> tuple[Chunk, ...]:
        """将 block 列表按 chunk_size 聚合成 chunk 列表。"""
        if not blocks:
            return ()

        chunks: list[Chunk] = []
        chunk_start = 0  # 当前 chunk 起始 block 的 index
        chunk_chars = 0  # 当前 chunk 累计字符数
        chunk_size = self.config.chunk_size
        active_page_label: str | None = None
        chunk_page_label: str | None = None

        for block in blocks:
            block_chars = len(block.text)
            if block.block_kind in self.config.hard_boundary_block_kinds:
                if block.block_kind == BlockKind.PAGE_MARKER:
                    if block.block_index > chunk_start and chunk_chars > 0:
                        chunks.append(
                            self._build_chunk(
                                blocks,
                                chunk_start,
                                block.block_index - 1,
                                len(chunks),
                                page_label=chunk_page_label,
                            )
                        )
                    if page_label := _extract_page_label(block):
                        active_page_label = page_label
                    chunk_page_label = active_page_label
                    chunk_start = block.block_index + 1
                    chunk_chars = 0
                    continue

                if block.block_index > chunk_start and chunk_chars > 0:
                    chunks.append(
                        self._build_chunk(
                            blocks,
                            chunk_start,
                            block.block_index - 1,
                            len(chunks),
                            page_label=chunk_page_label,
                        )
                    )
                if page_label := _extract_page_label(block):
                    active_page_label = page_label
                chunk_page_label = active_page_label
                chunk_start = block.block_index
                chunk_chars = 0

            # 如果加入当前 block 会超限，且当前 chunk 不为空，则切分
            if block.block_index > chunk_start and chunk_chars + block_chars > chunk_size:
                chunks.append(
                    self._build_chunk(
                        blocks,
                        chunk_start,
                        block.block_index - 1,
                        len(chunks),
                        page_label=chunk_page_label,
                    )
                )
                chunk_start = block.block_index
                chunk_chars = 0
                chunk_page_label = active_page_label
            chunk_chars += block_chars

        # 处理最后一个 chunk
        if chunk_start < len(blocks):
            chunks.append(
                self._build_chunk(
                    blocks,
                    chunk_start,
                    len(blocks) - 1,
                    len(chunks),
                    page_label=chunk_page_label,
                )
            )

        return tuple(chunks)

    def _build_chunk(
            self,
            blocks: tuple[TextBlock, ...],
            start_block: int,
            end_block: int,
            chunk_index: int,
            *,
            page_label: str | None = None,
    ) -> Chunk:
        """从 blocks[start_block..end_block] 构建一个 Chunk。"""
        selected = blocks[start_block:end_block + 1]
        # 用 separator 连接所有 block 文本
        text = self.config.separator.join(block.text for block in selected if block.text).strip()
        # 收集元信息
        block_kinds = tuple(block.block_kind for block in selected)
        section_paths = tuple(
            path
            for path in dict.fromkeys(block.section_path for block in selected if block.section_path)
        )
        titles = tuple(
            str(title)
            for title in (
                block.metadata.get("title")
                for block in selected
                if block.metadata.get("title")
            )
        )

        return Chunk(
            chunk_id=f"{self.config.chunk_id_prefix}-{chunk_index}",
            text=text,
            chunk_index=chunk_index,
            role=self.config.role,
            start_offset=selected[0].start_offset,
            end_offset=selected[-1].end_offset,
            start_block=selected[0].block_index,
            end_block=selected[-1].block_index,
            metadata={
                "block_kinds": block_kinds,
                "block_types": block_kinds,
                "start_block": selected[0].block_index,
                "end_block": selected[-1].block_index,
                "section_paths": section_paths,
                **({"titles": titles} if titles else {}),
                **({"page_label": page_label} if page_label else {}),
            },
        )


def _extract_page_label(block: TextBlock) -> str | None:
    page_label = block.metadata.get("page_number")
    return str(page_label) if page_label is not None else None
