from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from common.utils.chunkers import (
    BlockKind,
    ChunkDocument,
    LocatorKind,
    MarkdownChunker,
    SourceSpan,
    TextBlock,
)
from .models import (
    RagContentProjection,
    RagContentLocator,
    RagDocumentContent,
    RagRetrievalChunk,
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
)


# 标题树 → Section ReadingBlock → 检索子块 的投影流水线。
# 三个独立的 MarkdownChunker 负责不同粒度的切分：
#   - structure_chunker：按标题语义切出 Heading 边界，用于构建 Section 树。
#   - reading_block_chunker：在单个 Section 内部再次切分，避免长 Section 一次性返回过大正文。
#   - retrieval_chunker：在 ReadingBlock 内切出 embedding/BM25 用的更小检索子块。


class RagSectionProjector:
    """将 Markdown 投影为标题树、Section 阅读块和检索子块。"""

    __slots__ = ("_reading_block_chunker", "_retrieval_chunker", "_structure_chunker")

    def __init__(
        self,
        *,
        structure_chunker: MarkdownChunker | None = None,
        reading_block_chunker: MarkdownChunker | None = None,
        retrieval_chunker: MarkdownChunker | None = None,
    ) -> None:
        self._structure_chunker = structure_chunker or MarkdownChunker()
        self._reading_block_chunker = reading_block_chunker or MarkdownChunker()
        # 检索子块比 Section 更小，固定 800 字符硬上限。
        self._retrieval_chunker = retrieval_chunker or MarkdownChunker(max_characters=800)

    def project(self, content: RagDocumentContent) -> RagContentProjection:
        # 第一次 chunk 解析只为得到 Heading 边界和 locator，blocks 留作 Section 树构建。
        structure = self._structure_chunker.chunk(
            document=ChunkDocument(
                text=content.markdown,
                document_id=content.resource_id,
                content_type="text/markdown",
            )
        )
        content_starts_by_section_start = _section_content_starts(structure.blocks)
        sections = tuple(
            replace(
                section,
                preview=_section_preview(
                    content.markdown,
                    section,
                    content_starts_by_section_start,
                ),
            )
            for section in _build_sections(
                structure.blocks,
                resource_id=content.resource_id,
                document_version=content.document_version,
                text_length=len(content.markdown),
            )
        )

        reading_blocks: list[RagSectionReadingBlock] = []
        retrieval_chunks: list[RagRetrievalChunk] = []
        source_refs: list[RagSourceRef] = []
        content_locators = tuple(
            RagContentLocator(
                locator_index=index,
                name=locator.name,
                kind=locator.kind,
                start_offset=locator.start_offset,
                end_offset=locator.end_offset,
            )
            for index, locator in enumerate(structure.locators)
            if locator.start_offset is not None and locator.end_offset is not None
        )
        # 记录每个 Section 当前的 block ordinal，长 Section 会跨多个 block 编号递增。
        section_ordinals: dict[str, int] = {}

        for section in sections:
            if section.own_start == section.own_end:
                continue
            # 在 Section 局部坐标下做切分，然后平移回 Markdown 全文坐标。
            section_text = content.markdown[section.own_start: section.own_end]
            section_result = self._reading_block_chunker.chunk(
                document=ChunkDocument(
                    text=section_text,
                    document_id=section.section_id,
                    content_type="text/markdown",
                )
            )
            for block_chunk in section_result.chunks:
                block_spans = tuple(
                    SourceSpan(
                        start_offset=span.start_offset + section.own_start,
                        end_offset=span.end_offset + section.own_start,
                    )
                    for span in block_chunk.source_spans
                )
                # _render_source 同时返回 raw_text 与 local→source 坐标映射，
                # 后续检索子块的 source_spans 需要靠这个映射把局部坐标平移回原文。
                raw_text, source_map = _render_source(content.markdown, block_spans)
                block_locators = tuple(
                    locator
                    for locator in structure.locators
                    if locator.start_offset is not None
                    and locator.end_offset is not None
                    and any(
                        span.start_offset < locator.end_offset and span.end_offset > locator.start_offset
                        for span in block_spans
                    )
                )
                ordinal = section_ordinals.get(section.section_id, 0)
                reading_block = RagSectionReadingBlock(
                    block_id=_reading_block_id(content, section.section_id, block_spans),
                    section_id=section.section_id,
                    ordinal=ordinal,
                    raw_text=raw_text,
                    source_spans=block_spans,
                    page_labels=tuple(
                        dict.fromkeys(
                            locator.name.removeprefix("page:")
                            for locator in block_locators
                            if locator.kind is LocatorKind.PAGE
                        )
                    ),
                    anchor_labels=tuple(
                        dict.fromkeys(
                            locator.name.removeprefix("anchor:")
                            for locator in block_locators
                            if locator.kind is LocatorKind.ANCHOR
                        )
                    ),
                )
                reading_blocks.append(reading_block)
                section_ordinals[section.section_id] = ordinal + 1

                # 第三次切分用 reading_block 的 raw_text 作为输入，
                # 通过 source_map 把检索子块的局部 span 平移回 Markdown 全文坐标。
                child_result = self._retrieval_chunker.chunk(
                    document=ChunkDocument(
                        text=raw_text,
                        document_id=reading_block.block_id,
                        content_type="text/markdown",
                    )
                )
                for child in child_result.chunks:
                    source_spans = tuple(
                        dict.fromkeys(
                            SourceSpan(
                                start_offset=source_start + max(local.start_offset, local_start) - local_start,
                                end_offset=source_start + min(local.end_offset, local_end) - local_start,
                            )
                            for local in child.source_spans
                            for local_start, local_end, source_start in source_map
                            if local.start_offset < local_end and local.end_offset > local_start
                        )
                    )
                    child_raw_text = "\n\n".join(
                        content.markdown[span.start_offset: span.end_offset] for span in source_spans
                    )
                    child_index_text = (
                        f"Section: {' > '.join(section.section_path)}\nSection preview: {section.preview}\n\n{child_raw_text}"
                        if section.section_path
                        else child_raw_text
                    )
                    child_locators = tuple(
                        locator
                        for locator in block_locators
                        if locator.start_offset is not None
                        and locator.end_offset is not None
                        and any(
                            span.start_offset < locator.end_offset and span.end_offset > locator.start_offset
                            for span in source_spans
                        )
                    )
                    retrieval_chunk = RagRetrievalChunk(
                        chunk_id=_retrieval_chunk_id(reading_block.block_id, source_spans),
                        chunk_index=len(retrieval_chunks),
                        reading_block_id=reading_block.block_id,
                        section_id=section.section_id,
                        section_path=section.section_path,
                        raw_text=child_raw_text,
                        index_text=child_index_text,
                        source_spans=source_spans,
                        page_labels=tuple(
                            dict.fromkeys(
                                locator.name.removeprefix("page:")
                                for locator in child_locators
                                if locator.kind is LocatorKind.PAGE
                            )
                        ),
                        anchor_labels=tuple(
                            dict.fromkeys(
                                locator.name.removeprefix("anchor:")
                                for locator in child_locators
                                if locator.kind is LocatorKind.ANCHOR
                            )
                        ),
                    )
                    retrieval_chunks.append(retrieval_chunk)
                    # 1:1 关系：每个 retrieval chunk 对应一个 SourceRef，便于证据回源。
                    ref_key = "\0".join((retrieval_chunk.chunk_id, section.section_id))
                    source_refs.append(
                        RagSourceRef(
                            ref_id=f"rsrc_{sha256(ref_key.encode('utf-8')).hexdigest()[:32]}",
                            resource_id=content.resource_id,
                            document_version=content.document_version,
                            chunk_id=retrieval_chunk.chunk_id,
                            section_id=section.section_id,
                            section_path=section.section_path,
                            source_spans=retrieval_chunk.source_spans,
                            page_labels=retrieval_chunk.page_labels,
                            anchor_labels=retrieval_chunk.anchor_labels,
                        )
                    )

        return RagContentProjection(
            resource_id=content.resource_id,
            document_version=content.document_version,
            content_hash=sha256(content.markdown.encode("utf-8")).hexdigest(),
            markdown=content.markdown,
            reading_blocks=tuple(reading_blocks),
            retrieval_chunks=tuple(retrieval_chunks),
            sections=sections,
            source_refs=tuple(source_refs),
            locators=content_locators,
        )


def _render_source(
        markdown: str,
        source_spans: tuple[SourceSpan, ...],
) -> tuple[str, tuple[tuple[int, int, int], ...]]:
    """拼接 raw_text 并返回 local→source 坐标映射。

    返回的每条 mapping 是 (local_start, local_end, source_start)：
      - local_start / local_end：raw_text 中的区间；
      - source_start：原始 Markdown 中的起始 offset（end 由 local 长度隐含还原）。
    """
    fragments: list[str] = []
    source_map: list[tuple[int, int, int]] = []
    cursor = 0
    for span in source_spans:
        if fragments:
            cursor += 2  # 与 "\n\n".join 的分隔符保持一致。
        fragment = markdown[span.start_offset: span.end_offset]
        fragments.append(fragment)
        source_map.append((cursor, cursor + len(fragment), span.start_offset))
        cursor += len(fragment)
    return "\n\n".join(fragments), tuple(source_map)


def _build_sections(
        blocks: tuple[TextBlock, ...],
        *,
        resource_id: str,
        document_version: int,
        text_length: int,
) -> tuple[RagSectionNode, ...]:
    """根据文档标题块构建层级化 Section 树。

    Section 使用标题作为边界：
    - own_start / own_end 表示当前标题直属覆盖范围；
    - subtree_end 表示整个子树覆盖范围，会在遇到同级或更高层标题时闭合。
    """
    headings = tuple(block for block in blocks if block.block_kind is BlockKind.HEADING)

    # 文档开头到第一个标题之间的内容属于 root section。
    first_heading_start = headings[0].start_offset if headings else text_length
    assert first_heading_start is not None

    # root 节点不参与前缀式 ID 生成，但需要为顶层标题提供一个公共父节点和统一 subtree_end。
    root = RagSectionNode(
        section_id=_section_id(resource_id, document_version, "root"),
        resource_id=resource_id,
        document_version=document_version,
        title="",
        level=0,
        parent_section_id=None,
        ordinal=0,
        section_path=(),
        preview="",
        own_start=0,
        own_end=first_heading_start,
        subtree_end=text_length,
    )

    sections = [root]
    # open_sections 保存当前仍未闭合的标题链，栈顶即当前父级 Section 的索引。
    open_sections: list[int] = []
    # 记录每个父节点下已创建的子节点数，用于稳定排序。
    child_counts: dict[str, int] = {}

    for heading_index, heading in enumerate(headings):
        assert heading.start_offset is not None
        level = int(heading.metadata["heading_level"])

        # 遇到同级或更高层标题时，原节点的 subtree_end 收敛到该标题开始处。
        while open_sections and sections[open_sections[-1]].level >= level:
            closed_index = open_sections.pop()
            sections[closed_index] = replace(
                sections[closed_index],
                subtree_end=heading.start_offset,
            )

        parent = sections[open_sections[-1]] if open_sections else root
        ordinal = child_counts.get(parent.section_id, 0)
        child_counts[parent.section_id] = ordinal + 1

        # own 范围从当前标题开始，到下一个标题开始（不区分层级）为止。
        next_heading_start = (
            headings[heading_index + 1].start_offset
            if heading_index + 1 < len(headings)
            else text_length
        )
        assert next_heading_start is not None

        section = RagSectionNode(
            section_id=_section_id(
                resource_id,
                document_version,
                str(heading.start_offset),
            ),
            resource_id=resource_id,
            document_version=document_version,
            title=str(heading.metadata["title"]),
            level=level,
            parent_section_id=parent.section_id,
            ordinal=ordinal,
            section_path=heading.section_path,
            preview="",
            own_start=heading.start_offset,
            own_end=next_heading_start,
            subtree_end=text_length,
        )
        sections.append(section)
        open_sections.append(len(sections) - 1)

    return tuple(sections)


def _section_id(resource_id: str, document_version: int, source_key: str) -> str:
    """根据资源版本和标题位置生成稳定 Section ID。"""
    value = "\0".join((resource_id, str(document_version), "section", source_key))
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"rsec_{digest[:32]}"


def _section_content_starts(
        blocks: tuple[TextBlock, ...],
) -> dict[int, int]:
    content_starts: dict[int, int] = {}
    for block in blocks:
        if block.block_kind is not BlockKind.HEADING:
            continue
        assert block.start_offset is not None
        assert block.end_offset is not None
        content_starts[block.start_offset] = block.end_offset
    return content_starts


def _section_preview(
        markdown: str,
        section: RagSectionNode,
        content_starts_by_section_start: dict[int, int],
) -> str:
    # 标题本身已有 title/section_path 承载，preview 只暴露当前 Section 的直属正文。
    start_offset = content_starts_by_section_start.get(section.own_start, section.own_start)
    return " ".join(markdown[start_offset: section.own_end].split())[:500]


def _reading_block_id(
        content: RagDocumentContent,
        section_id: str,
        source_spans: tuple[SourceSpan, ...],
) -> str:
    """根据资源版本和 Section 内部 span 范围生成稳定 ID。"""
    spans = ";".join(f"{span.start_offset}:{span.end_offset}" for span in source_spans)
    value = "\0".join(
        (
            content.resource_id,
            str(content.document_version),
            section_id,
            "reading_block",
            spans,
        )
    )
    return f"rsb_{sha256(value.encode('utf-8')).hexdigest()[:32]}"


def _retrieval_chunk_id(
        reading_block_id: str,
        source_spans: tuple[SourceSpan, ...],
) -> str:
    """在 ReadingBlock 命名空间下为检索子块生成稳定 ID。"""
    spans = ";".join(f"{span.start_offset}:{span.end_offset}" for span in source_spans)
    value = f"{reading_block_id}\0retrieval_chunk\0{spans}"
    return f"rrc_{sha256(value.encode('utf-8')).hexdigest()[:32]}"
