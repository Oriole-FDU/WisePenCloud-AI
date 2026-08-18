from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from .models import (
    Anchor,
    BlockKind,
    DocumentBlock,
    DocumentChunk,
    DocumentChunkingResult,
    Page,
    Section,
    SourceSpan,
)
from .parser import DocumentParser
from .recursive_splitter import split_plain_text


@dataclass(frozen=True, slots=True)
class DocumentChunkerConfig:
    """文档 chunk 的硬上限和 oversized block overlap 配置。"""

    max_characters: int = 6000
    chunk_overlap: int = 0

    def __post_init__(self) -> None:
        if self.max_characters <= 0:
            raise ValueError("max_characters must be positive")
        if not 0 <= self.chunk_overlap < self.max_characters:
            raise ValueError(
                "chunk_overlap must be between 0 and max_characters"
            )


class DocumentChunker:
    """将 Markdown-compatible 文本解析为 chunks 和确定性文档结构。"""

    __slots__ = ("_parser", "config")

    def __init__(self, config: DocumentChunkerConfig | None = None) -> None:
        self.config = config or DocumentChunkerConfig()
        self._parser = DocumentParser()

    def chunk(self, text: str) -> DocumentChunkingResult:
        # 解析、页/锚点收集和分块共享同一批 blocks，保证所有结构事实使用同一套原文坐标。
        blocks = self._parser.parse(text)
        pages = _build_pages(text_length=len(text), blocks=blocks)
        anchors = _build_anchors(blocks)
        chunks = self._chunk_semantic_sections(blocks)

        if any(block.block_kind is BlockKind.HEADING for block in blocks):
            sections = _build_heading_sections(text=text, blocks=blocks)
            chunks = tuple(
                replace(
                    chunk,
                    section_ids=tuple(
                        section.section_id
                        for section in sections
                        if _overlaps_any(section.own_span, chunk.source_spans)
                    ),
                )
                for chunk in chunks
            )
        else:
            sections = _build_flat_sections(text=text, chunks=chunks)
            chunks = tuple(
                replace(chunk, section_ids=(sections[index].section_id,))
                for index, chunk in enumerate(chunks)
            )

        return DocumentChunkingResult(
            chunks=chunks,
            blocks=blocks,
            sections=sections,
            pages=pages,
            anchors=anchors,
        )

    def _chunk_semantic_sections(
        self,
        blocks: tuple[DocumentBlock, ...],
    ) -> tuple[DocumentChunk, ...]:
        """标题只划分 Section；页标不会切断语义 chunk。"""
        chunks: list[DocumentChunk] = []
        section_blocks: list[DocumentBlock] = []
        section_has_body = False

        def flush_section() -> None:
            nonlocal section_has_body
            if not section_blocks:
                return
            # 标题只负责结束已有正文 Section；页标被跳过，不会强制切断语义 chunk。
            chunks.extend(self._pack_blocks(tuple(section_blocks)))
            section_blocks.clear()
            section_has_body = False

        for block in blocks:
            if block.block_kind is BlockKind.PAGE_MARKER:
                continue
            if block.block_kind is BlockKind.HEADING and section_has_body:
                flush_section()
            section_blocks.append(block)
            if block.block_kind is not BlockKind.HEADING:
                section_has_body = True

        flush_section()
        return _assign_chunk_ids(tuple(chunks))

    def _pack_blocks(
        self,
        blocks: tuple[DocumentBlock, ...],
    ) -> tuple[DocumentChunk, ...]:
        """在完整 block 边界装箱，仅 oversized block 内部允许 overlap。"""
        chunks: list[DocumentChunk] = []
        selected: list[DocumentBlock] = []
        selected_chars = 0

        def flush() -> None:
            nonlocal selected_chars
            if not selected:
                return
            chunks.append(_build_chunk(tuple(selected)))
            selected.clear()
            selected_chars = 0

        for block in blocks:
            for part in self._split_oversized_block(block):
                # 普通 block 先保持完整再装箱；只有 part 来自 oversized block 时，
                # 才可能携带递归切分产生的 overlap。
                separator_chars = 1 if selected else 0
                part_chars = len(part.text) + separator_chars
                if selected and selected_chars + part_chars > self.config.max_characters:
                    flush()
                    part_chars = len(part.text)

                selected.append(part)
                selected_chars += part_chars

        flush()
        return tuple(chunks)

    def _split_oversized_block(
        self,
        block: DocumentBlock,
    ) -> tuple[DocumentBlock, ...]:
        if len(block.text) <= self.config.max_characters:
            return (block,)

        # parser 已经确定这是完整 Markdown block。超预算时无法继续保持 block 原子性，
        # 因此沿用纯文本分隔符递归切分，并把局部 span 平移回全文坐标。
        parts = split_plain_text(
            block.text,
            chunk_size=self.config.max_characters,
            chunk_overlap=self.config.chunk_overlap,
        )
        return tuple(
            replace(
                block,
                block_id=f"{block.block_id}:part:{index}",
                text=part.text,
                start_offset=block.start_offset + part.start_offset,
                end_offset=block.start_offset + part.end_offset,
            )
            for index, part in enumerate(parts)
        )


def _build_chunk(selected: tuple[DocumentBlock, ...]) -> DocumentChunk:
    source_spans = tuple(
        SourceSpan(block.start_offset, block.end_offset) for block in selected
    )
    return DocumentChunk(
        chunk_id="pending",
        text="\n".join(block.text for block in selected).strip(),
        chunk_index=0,
        start_offset=source_spans[0].start_offset,
        end_offset=source_spans[-1].end_offset,
        source_spans=source_spans,
        start_block=selected[0].block_index,
        end_block=selected[-1].block_index,
        page_labels=tuple(
            dict.fromkeys(
                str(page_label)
                for block in selected
                if (page_label := block.metadata.get("page_label")) is not None
            )
        ),
        anchor_labels=tuple(
            dict.fromkeys(
                str(anchor_label)
                for block in selected
                if (anchor_label := block.metadata.get("anchor_label"))
                is not None
            )
        ),
    )


def _assign_chunk_ids(
    chunks: tuple[DocumentChunk, ...],
) -> tuple[DocumentChunk, ...]:
    finalized: list[DocumentChunk] = []
    for index, chunk in enumerate(chunks):
        content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        finalized.append(
            replace(
                chunk,
                chunk_id=f"chunk:{index}:{content_hash[:16]}",
                chunk_index=index,
                content_hash=content_hash,
            )
        )
    return tuple(finalized)


def _build_pages(
    *,
    text_length: int,
    blocks: tuple[DocumentBlock, ...],
) -> tuple[Page, ...]:
    # 一个页标的范围延伸到下一个页标（或文档末尾）；页标本身仍保留在范围起点，
    # 这样 page_range 可以通过 span 相交计算，而不需要切断 chunk 正文。
    markers = [
        block for block in blocks if block.block_kind is BlockKind.PAGE_MARKER
    ]
    return tuple(
        Page(
            page_index=index,
            page_label=str(marker.metadata["page_label"]),
            source_span=SourceSpan(
                marker.start_offset,
                (
                    markers[index + 1].start_offset
                    if index + 1 < len(markers)
                    else text_length
                ),
            ),
        )
        for index, marker in enumerate(markers)
    )


def _build_anchors(blocks: tuple[DocumentBlock, ...]) -> tuple[Anchor, ...]:
    # anchor_label 已由 parser 识别并挂到完整 block，这里只投影其精确源范围。
    return tuple(
        Anchor(
            label=str(block.metadata["anchor_label"]),
            source_span=SourceSpan(block.start_offset, block.end_offset),
        )
        for block in blocks
        if block.metadata.get("anchor_label")
    )


def _build_heading_sections(
    *,
    text: str,
    blocks: tuple[DocumentBlock, ...],
) -> tuple[Section, ...]:
    headings = [
        block for block in blocks if block.block_kind is BlockKind.HEADING
    ]
    first_heading_start = headings[0].start_offset
    root_content_spans = _content_spans(blocks, 0, first_heading_start)
    # 第一个标题前的正文没有真实父标题，使用 level=0 的虚拟 root 承接；
    # 有内容时给它“文档开头”标题，空 root 则由 OutlineAssembler 隐藏。
    root_title = "文档开头" if root_content_spans else ""
    root = Section(
        section_id=_section_id("root", 0, first_heading_start),
        title=root_title,
        level=0,
        parent_section_id=None,
        ordinal=0,
        section_path=(root_title,) if root_title else (),
        own_span=SourceSpan(0, first_heading_start),
        subtree_span=SourceSpan(0, len(text)),
        content_spans=root_content_spans,
        preview=_section_preview(text, root_content_spans),
    )
    sections = [root]
    open_section_indexes: list[int] = []
    child_counts: dict[str, int] = {}

    for heading_index, heading in enumerate(headings):
        level = int(heading.metadata["heading_level"])
        # 栈内保存当前路径上尚未闭合的 Section 下标。弹出所有同级/更深标题，
        # 随后栈顶就是最近的合法父节点。
        while (
            open_section_indexes
            and sections[open_section_indexes[-1]].level >= level
        ):
            closed_index = open_section_indexes.pop()
            closed = sections[closed_index]
            sections[closed_index] = replace(
                closed,
                subtree_span=SourceSpan(
                    closed.subtree_span.start_offset,
                    heading.start_offset,
                ),
            )

        parent = (
            sections[open_section_indexes[-1]]
            if open_section_indexes
            else root
        )
        ordinal = child_counts.get(parent.section_id, 0)
        child_counts[parent.section_id] = ordinal + 1
        own_end = (
            headings[heading_index + 1].start_offset
            if heading_index + 1 < len(headings)
            else len(text)
        )
        # own_span 只到下一个标题；subtree_span 先延伸到文档末尾，
        # 遇到同级或更高标题时再回填为闭合位置。
        content_spans = _content_spans(blocks, heading.end_offset, own_end)
        section = Section(
            section_id=_section_id("heading", heading.start_offset, own_end),
            title=str(heading.metadata["title"]),
            level=level,
            parent_section_id=parent.section_id,
            ordinal=ordinal,
            section_path=heading.section_path,
            own_span=SourceSpan(heading.start_offset, own_end),
            subtree_span=SourceSpan(heading.start_offset, len(text)),
            content_spans=content_spans,
            preview=_section_preview(text, content_spans),
        )
        sections.append(section)
        open_section_indexes.append(len(sections) - 1)

    return tuple(sections)


def _build_flat_sections(
    *,
    text: str,
    chunks: tuple[DocumentChunk, ...],
) -> tuple[Section, ...]:
    # 无标题输入退化为 chunk 对应的 synthetic Section，便于按 section_id 导航。
    sections: list[Section] = []
    for index, chunk in enumerate(chunks):
        title = f"全文片段 {index + 1}"
        own_span = SourceSpan(chunk.start_offset, chunk.end_offset)
        sections.append(
            Section(
                section_id=_section_id(
                    "flat_text",
                    own_span.start_offset,
                    own_span.end_offset,
                ),
                title=title,
                level=1,
                parent_section_id=None,
                ordinal=index,
                section_path=(title,),
                own_span=own_span,
                subtree_span=own_span,
                content_spans=chunk.source_spans,
                preview=_section_preview(text, chunk.source_spans),
            )
        )
    return tuple(sections)


def _content_spans(
    blocks: tuple[DocumentBlock, ...],
    start_offset: int,
    end_offset: int,
) -> tuple[SourceSpan, ...]:
    # 标题和页标不算 Section 直属正文；子 Section 的 block 也由边界过滤排除。
    return tuple(
        SourceSpan(block.start_offset, block.end_offset)
        for block in blocks
        if block.block_kind not in {BlockKind.HEADING, BlockKind.PAGE_MARKER}
        and block.text.strip()
        and start_offset <= block.start_offset
        and block.end_offset <= end_offset
    )


def _section_preview(text: str, spans: tuple[SourceSpan, ...]) -> str:
    return " ".join(
        text[span.start_offset : span.end_offset].strip() for span in spans
    )[:500]


def _section_id(kind: str, start_offset: int, end_offset: int) -> str:
    # ID 只依赖文档局部结构和 span；重复标题路径因原文边界不同仍保持唯一。
    identity = f"{kind}\0{start_offset}\0{end_offset}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"sec_{digest[:16]}"


def _overlaps_any(span: SourceSpan, others: tuple[SourceSpan, ...]) -> bool:
    return any(
        span.start_offset < other.end_offset
        and span.end_offset > other.start_offset
        for other in others
    )
