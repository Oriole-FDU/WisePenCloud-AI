from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from blingfire import text_to_sentences

from ..parsing.parser import MarkdownNode, MarkdownNodeKind, MarkdownParser, SourceSpan
from .packer import ChunkPacker, MarkdownChunk
from .splitter import StructuralNodeSplitter
from .tokenizer import TokenCounter, default_token_counter


@dataclass(frozen=True, slots=True)
class Anchor:
    """表格、图片或公式锚点及其精确原文范围。"""

    label: str
    source_span: SourceSpan


@dataclass(frozen=True, slots=True)
class Section:
    """标题树中的 Section 及其直属正文和子树范围。"""

    section_id: str
    title: str
    level: int
    parent_section_id: str | None
    ordinal: int
    section_path: list[str]
    own_span: SourceSpan
    subtree_span: SourceSpan
    content_spans: list[SourceSpan] = field(default_factory=list)
    summary: str = ""  # 可以接入llm summarize等
    preview: str = ""


@dataclass(frozen=True, slots=True)
class MarkdownChunkingResult:
    """一次文档解析和分块产生的结构事实。"""

    chunks: list[MarkdownChunk]
    nodes: list[MarkdownNode]
    sections: list[Section]
    anchors: list[Anchor]


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    """Markdown 分块输入；title 是外部文档标题，不要求出现在正文中。"""

    text: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class MarkdownChunkerConfig:
    """Chunking 目标尺寸与结构拆分触发阈值。"""

    target_chunk_tokens: int = 800
    split_threshold_tokens: int = 1600

    def __post_init__(self) -> None:
        if self.target_chunk_tokens <= 0:
            raise ValueError("target_chunk_tokens must be positive")
        if self.split_threshold_tokens < self.target_chunk_tokens:
            raise ValueError("split_threshold_tokens must be >= target_chunk_tokens")


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    """Splitter 和 Packer 共用的预算取舍，不承诺结构单元必须小于阈值。"""

    target_chunk_tokens: int
    split_threshold_tokens: int
    token_counter: TokenCounter

    def should_append(self, *, current_tokens: int, candidate_tokens: int) -> bool:
        """未达软目标时按距离组合；已达目标则封存，候选不能超过硬阈值。"""
        # 即使 BPE 非可加性使追加后 token 数减少，也不再扩充已达软目标的结构。
        return (
            current_tokens < self.target_chunk_tokens
            and candidate_tokens <= self.split_threshold_tokens
            and abs(candidate_tokens - self.target_chunk_tokens)
            <= abs(current_tokens - self.target_chunk_tokens)
        )


class MarkdownChunker:
    """编排解析、章节组织、结构拆分和 Chunk 装箱，不参与预算取舍。"""

    __slots__ = ("_packer", "_parser", "_splitter", "config")

    def __init__(
        self,
        config: MarkdownChunkerConfig | None = None,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.config = config or MarkdownChunkerConfig()
        self._parser = MarkdownParser()
        policy = ChunkingPolicy(
            target_chunk_tokens=self.config.target_chunk_tokens,
            split_threshold_tokens=self.config.split_threshold_tokens,
            token_counter=token_counter or default_token_counter(),
        )
        self._splitter = StructuralNodeSplitter(policy)
        self._packer = ChunkPacker(policy)

    def chunk(self, document: str | MarkdownDocument) -> MarkdownChunkingResult:
        """解析并分块文档；纯字符串输入保留旧接口，结构输入可提供标题。"""
        if isinstance(document, str):
            document = MarkdownDocument(document)

        nodes = self._parser.parse(document.text)
        anchors = _build_anchors(nodes)
        sections = (
            _build_heading_sections(
                text=document.text,
                nodes=nodes,
                root_title=document.title,
            )
            if any(node.kind is MarkdownNodeKind.SECTION for node in nodes)
            else []
        )
        return MarkdownChunkingResult(
            chunks=self._chunk_by_sections(nodes, sections),
            nodes=nodes,
            sections=sections,
            anchors=anchors,
        )

    def _chunk_by_sections(
        self,
        nodes: list[MarkdownNode],
        sections: list[Section],
    ) -> list[MarkdownChunk]:
        """标题只切换章节；正文依次经过 Splitter 与 Packer。"""
        chunks: list[MarkdownChunk] = []
        section_nodes: list[MarkdownNode] = []
        section_index = 0
        current_section = None
        if sections and sections[0].level == 0:
            current_section = sections[0]
            section_index = 1

        def flush() -> None:
            if section_nodes:
                units = (
                    part
                    for node in section_nodes
                    for part in self._splitter.split(node)
                )
                chunks.extend(
                    self._packer.pack(
                        units, section=current_section, start_index=len(chunks)
                    )
                )
                section_nodes.clear()

        for node in nodes:
            if node.kind is MarkdownNodeKind.SECTION:
                flush()
                current_section = sections[section_index]
                section_index += 1
                continue
            section_nodes.append(node)
        flush()
        return chunks


def _build_anchors(nodes: list[MarkdownNode]) -> list[Anchor]:
    """从带 anchor_label 的节点提取锚点，跨度取源文最小起点/最大终点。"""
    anchors: list[Anchor] = []
    for node in nodes:
        label = node.metadata.get("anchor_label")
        if label is None or not node.source_spans:
            continue
        anchors.append(
            Anchor(
                label=str(label),
                source_span=SourceSpan(
                    min(span.start_offset for span in node.source_spans),
                    max(span.end_offset for span in node.source_spans),
                ),
            )
        )
    return anchors


def _build_heading_sections(
    *,
    text: str,
    nodes: list[MarkdownNode],
    root_title: str | None = None,
) -> list[Section]:
    """根据标题层级构建章节树，并计算各级 section 的 own_span / subtree_span。"""
    headings = [node for node in nodes if node.kind is MarkdownNodeKind.SECTION]
    first_heading_start = headings[0].start

    # 首个标题之前的内容归入根章节；外部标题追加 <文档开头> 标记，与无标题时的兼容名称区分。
    root_content_spans = _content_spans(nodes, 0, first_heading_start)
    root_name = f"{root_title}<文档开头>" if root_title else "文档开头"
    root = (
        Section(
            section_id=_section_id("root", 0, first_heading_start),
            title=root_name,
            level=0,
            parent_section_id=None,
            ordinal=0,
            section_path=[root_name],
            own_span=SourceSpan(0, first_heading_start),
            subtree_span=SourceSpan(0, len(text)),
            content_spans=root_content_spans,
        )
        if root_content_spans or root_title
        else None
    )
    sections = [root] if root else []

    open_indexes: list[int] = []  # 当前处于"打开"状态的祖先链
    child_counts: dict[str | None, int] = {}  # 每个父章节已分配的 ordinal 数

    for heading_index, heading in enumerate(headings):
        level = int(heading.metadata["heading_level"])
        heading_start = heading.start

        # 遇到同级或更高级标题：关闭所有层级 >= 当前的章节
        while open_indexes and sections[open_indexes[-1]].level >= level:
            closed_index = open_indexes.pop()
            closed = sections[closed_index]
            sections[closed_index] = replace(
                closed,
                subtree_span=SourceSpan(
                    closed.subtree_span.start_offset,
                    heading_start,
                ),
            )

        # 父章节为当前栈顶；栈空则挂在 root 下
        parent = sections[open_indexes[-1]] if open_indexes else root
        parent_id = parent.section_id if parent else None
        ordinal = child_counts.get(parent_id, 0)
        child_counts[parent_id] = ordinal + 1

        # own_span 到下一个标题（或文末）为止
        own_end = (
            headings[heading_index + 1].start
            if heading_index + 1 < len(headings)
            else len(text)
        )

        section = Section(
            section_id=_section_id("heading", heading_start, own_end),
            title=str(heading.metadata["title"]),
            level=level,
            parent_section_id=parent_id,
            ordinal=ordinal,
            section_path=list(heading.metadata.get("section_path", [])),
            own_span=SourceSpan(heading_start, own_end),
            subtree_span=SourceSpan(heading_start, len(text)),  # 稍后回填
            content_spans=_content_spans(nodes, heading.end, own_end),
        )
        sections.append(section)
        open_indexes.append(len(sections) - 1)  # 压入祖先栈
    return [
        replace(section, preview=_build_section_preview(text, section))
        for section in sections
    ]


def _build_section_preview(text: str, section: Section) -> str:
    """从章节直属正文提取前两句，作为持久化的轻量导航预览。"""

    if not section.content_spans:
        return ""
    section_text = "\n".join(
        text[span.start_offset : span.end_offset] for span in section.content_spans
    )
    sentences = text_to_sentences(section_text).split("\n")
    return "".join(sentences[:2])[:120]


def _content_spans(
    nodes: list[MarkdownNode],
    start_offset: int,
    end_offset: int,
) -> list[SourceSpan]:
    """收集落在 [start_offset, end_offset] 内、非 SECTION 且有内容的节点跨度。"""
    return [
        SourceSpan(node.start, node.end)
        for node in nodes
        if node.kind is not MarkdownNodeKind.SECTION
        and node.text.strip()
        and node.source_spans
        and start_offset <= node.start
        and node.end <= end_offset
    ]


def _section_id(kind: str, start_offset: int, end_offset: int) -> str:
    """基于类型与区间生成稳定 section ID。"""
    identity = f"{kind}\0{start_offset}\0{end_offset}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"sec_{digest[:16]}"
