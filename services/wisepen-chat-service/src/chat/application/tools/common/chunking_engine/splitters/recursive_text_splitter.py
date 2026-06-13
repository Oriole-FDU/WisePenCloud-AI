from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter as LangchainSplitter

from ..core.models import ChunkDocument, TextUnit, UnitType


@dataclass(frozen=True, slots=True)
class RecursiveTextSplitterConfig:
    """递归文本切分器配置，透传至 langchain RecursiveCharacterTextSplitter。

    原理：按 separators 列表依次尝试切分，优先用大分隔符（如双换行），
    如果切出的块仍超过 chunk_size，则用更小的分隔符（如单换行、句号）继续切，
    直到满足大小要求或用完所有分隔符。
    """

    chunk_size: int = 4000  # 目标 chunk 字符数
    chunk_overlap: int = 100  # 相邻 chunk 重叠字符数，保证上下文连续性
    separators: tuple[str, ...] = ("\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", "")
    # 切分优先级：双换行 > 单换行 > 中文句号 > 英文句号 > 空格 > 逐字符

    @classmethod
    def for_markdown(
        cls,
        *,
        chunk_size: int = 4000,
        chunk_overlap: int = 100,
    ) -> RecursiveTextSplitterConfig:
        """Markdown 专用分隔符配置，优先按标题切分。"""
        return cls(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=("\n## ", "\n### ", "\n#### ", "\n\n", "\n", "。", ".", " ", ""),
        )


class RecursiveTextSplitter:
    """基于 langchain RecursiveCharacterTextSplitter 的切分器。

    适用于无结构文本（纯文本、日志等），直接按目标大小切分，
    不需要再经过 packer 聚合（pipeline 中 packer 设为 None 即可）。
    """

    __slots__ = ("config", "name", "_splitter")

    def __init__(self, config: RecursiveTextSplitterConfig | None = None) -> None:
        self.config = config or RecursiveTextSplitterConfig()
        self.name = "recursive_text_splitter"
        self._splitter = LangchainSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=list(self.config.separators),
        )

    def split(
        self,
        *,
        document: ChunkDocument,
    ) -> tuple[TextUnit, ...]:
        """将文本按递归分隔符切分为 TextUnit 列表。"""
        text = document.text
        if not text:
            return ()

        raw_chunks = self._splitter.split_text(text)
        units: list[TextUnit] = []
        cursor = 0  # 用于在原文中定位每个 chunk 的 offset
        for index, chunk_text in enumerate(raw_chunks):
            start = text.find(chunk_text, cursor)
            if start < 0:
                start = cursor
            end = start + len(chunk_text)
            units.append(
                TextUnit(
                    unit_id=f"unit-{index}",
                    text=chunk_text,
                    unit_type=UnitType.PARAGRAPH,
                    unit_index=index,
                    start_offset=start,
                    end_offset=end,
                )
            )
            cursor = end

        return tuple(units)
