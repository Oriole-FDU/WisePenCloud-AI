"""结构分块编排：章节构建、锚点提取与 chunk 拆分装箱"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from blingfire import text_to_sentences

from ..model import MarkdownNodeKind, Span, MarkdownNode
from ..parsing.parser import MarkdownParser
from .packer import ChunkPacker, MarkdownChunk
from .splitter import StructuralNodeSplitter
from .tokenizer import TokenCounter, default_token_counter


@dataclass(frozen=True, slots=True)
class Anchor:
    """表格、图片或公式锚点的标签与原文范围"""

    label: str
    source_span: Span


@dataclass(frozen=True, slots=True)
class Section:
    """标题树中的 Section 及其下属文本和下属子树范围

    own_span 为下属文本范围（标题起点到下一标题起点或文末）
    subtree_span 为下属子树范围（含所有子章节）
    根章节由第一个标题之前的内容构成，标题默认为「文档开头」，传入 title 时命名为「<title><文档开头>」
    """

    section_id: str
    title: str
    level: int
    parent_section_id: str | None
    ordinal: int
    section_path: list[str]
    own_span: Span
    subtree_span: Span
    content_spans: list[Span] = field(default_factory=list)
    summary: str = ""  # TODO: 未来可以接入llm summarize等
    preview: str = ""


@dataclass(frozen=True, slots=True)
class MarkdownChunkingResult:
    """文档解析和分块产生的结果"""

    chunks: list[MarkdownChunk]
    nodes: list[MarkdownNode]
    sections: list[Section]
    anchors: list[Anchor]


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    """决定一块内容该并进当前 chunk，还是另起一个新 chunk的规则

    按 token 数计算：
    target_chunk_tokens（软目标，默认 800）是 chunk 的推荐长度，装箱时尽量接近该值，非硬性要求
    split_threshold_tokens（硬上限，默认 1200）是 chunk 不许超过的上限；超过应该拆分，无法拆分的标记 overflow 保留原样
    """

    target_chunk_tokens: int  # 软目标：一个 chunk 的理想 token 数
    split_threshold_tokens: int  # 硬上限：超过就必须拆
    token_counter: TokenCounter  # 计算 token 数的计数器

    def should_append(self, *, current_tokens: int, candidate_tokens: int) -> bool:
        """判断内容是否追加进当前 chunk"""
        # 当前 chunk 还没到软目标（还有空间继续装）且 追加之后不超过硬上限 且 追加之后离软目标更近（越装越合适才装）
        return (
            current_tokens < self.target_chunk_tokens
            and candidate_tokens <= self.split_threshold_tokens
            and abs(candidate_tokens - self.target_chunk_tokens)
            <= abs(current_tokens - self.target_chunk_tokens)
        )


class MarkdownChunker:
    """编排解析、章节组织、结构拆分和 Chunk 装箱"""

    __slots__ = ("_packer", "_parser", "_splitter")

    def __init__(
        self,
        target_chunk_tokens: int = 800,
        split_threshold_tokens: int = 1200,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        if target_chunk_tokens <= 0:
            raise ValueError("target_chunk_tokens must be positive")
        if split_threshold_tokens < target_chunk_tokens:
            raise ValueError("split_threshold_tokens must be >= target_chunk_tokens")
        self._parser = MarkdownParser()
        policy = ChunkingPolicy(
            target_chunk_tokens=target_chunk_tokens,
            split_threshold_tokens=split_threshold_tokens,
            token_counter=token_counter or default_token_counter(),
        )
        self._splitter = StructuralNodeSplitter(policy)
        self._packer = ChunkPacker(policy)

    def chunk(self, text: str, title: str = "") -> MarkdownChunkingResult:
        """解析并分块文档"""
        nodes = self._parser.parse(text)
        sections = (
            _build_heading_sections(
                text=text,
                nodes=nodes,
                root_title=title,
            ) if any(node.kind is MarkdownNodeKind.SECTION for node in nodes) else []
        )
        return MarkdownChunkingResult(
            nodes=nodes,
            sections=sections,
            chunks=self._chunk_by_sections(nodes, sections),
            anchors=_build_anchors(nodes),
        )

    def _chunk_by_sections(
        self,
        nodes: list[MarkdownNode],
        sections: list[Section],
    ) -> list[MarkdownChunk]:
        """按章节把正文节点拆分、装箱成 chunk"""

        chunks: list[MarkdownChunk] = []  # 最终结果：所有章节的 chunk
        section_nodes: list[MarkdownNode] = []  # 缓冲区：当前章节的正文节点
        section_index = 0  # 下一个标题在 sections 中的下标
        current_section = None  # 当前正在装箱的章节

        # 若存在 level=0 的根章节（首个标题之前的内容），先把它设为当前章节，
        # 并把 section_index 拨到 1，使第一个真正的标题对应 sections[1]
        if sections and sections[0].level == 0:
            current_section = sections[0]
            section_index = 1

        def flush() -> None:
            """把缓冲区里的正文拆分、装箱，然后清空缓冲区"""
            if section_nodes:
                # 每个节点先拆分，再把拆出的 part 拍平成有序的装箱单元流
                units = (
                    part
                    for node in section_nodes
                    for part in self._splitter.split(node)
                )
                # 装箱时挂上当前章节信息
                # start_index 延续全局 chunk 编号
                chunks.extend(
                    self._packer.pack(
                        units, section=current_section, start_index=len(chunks)
                    )
                )
                section_nodes.clear()

        for node in nodes:
            if node.kind is MarkdownNodeKind.SECTION:
                flush()  # 封存上一章节的正文
                current_section = sections[section_index]  # 切到新章节
                section_index += 1
                continue  # 标题本身不进缓冲区
            section_nodes.append(node)  # 正文先攒着
        flush()  # 处理最后一章的剩余正文
        return chunks


def _build_anchors(nodes: list[MarkdownNode]) -> list[Anchor]:
    """从带 anchor_label 的节点提取锚点，原文区间取最小起点/最大终点"""
    anchors: list[Anchor] = []
    for node in nodes:
        label = node.metadata.get("anchor_label")  # 归一化时写入的编号题注标签
        if label is None or not node.source_spans:
            continue  # 无标签或没有原文区间的节点不产生锚点
        anchors.append(
            Anchor(
                label=str(label),
                # 一个节点可能含多个原文区间，取整体包络（最小起点到最大终点）
                source_span=Span(
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
    """根据标题层级构建章节树，并计算各级 section 的 own_span / subtree_span"""
    headings = [node for node in nodes if node.kind is MarkdownNodeKind.SECTION]
    first_heading_start = headings[0].start

    # 首个标题之前的内容归入根章节
    # 外部标题追加 <文档开头> 标记，与无标题时的兼容名称区分
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
            own_span=Span(0, first_heading_start),
            subtree_span=Span(0, len(text)),
            content_spans=root_content_spans,
        )
        # 只有首个标题前有正文、或外部给了标题时才创建根章节
        if root_content_spans or root_title else None
    )
    sections = [root] if root else []

    open_indexes: list[int] = []  # 当前打开的祖先章节下标，栈顶是最近的父章节
    child_counts: dict[str | None, int] = {}  # 每个父章节已分配的 ordinal 数

    for heading_index, heading in enumerate(headings):
        level = int(heading.metadata["heading_level"])
        heading_start = heading.start

        # 遇到同级或更高级标题
        # 把层级 >= 当前的祖先全部关闭，用当前标题的位置回填它们 subtree_span 的结束位置
        while open_indexes and sections[open_indexes[-1]].level >= level:
            closed_index = open_indexes.pop()
            closed = sections[closed_index]
            sections[closed_index] = replace(
                closed,
                subtree_span=Span(
                    closed.subtree_span.start_offset,
                    heading_start,
                ),
            )

        # 父章节取栈顶
        # 栈空说明挂在根章节下（无根章节则为 None）
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
            own_span=Span(heading_start, own_end),
            subtree_span=Span(heading_start, len(text)),  # 先写到文末，关闭时再回填
            content_spans=_content_spans(nodes, heading.end, own_end),
        )
        sections.append(section)
        open_indexes.append(len(sections) - 1)  # 新章节入栈，成为新的栈顶父章节

    # 统一回填每个章节的 preview
    return [
        replace(section, preview=_build_section_preview(text, section))
        for section in sections
    ]


def _build_section_preview(text: str, section: Section) -> str:
    """从章节下属正文提取前两句，作为持久化的轻量导航预览"""

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
) -> list[Span]:
    """收集落在 [start_offset, end_offset] 内、非 SECTION 且有内容的节点原文区间"""
    return [
        Span(node.start, node.end)
        for node in nodes
        if node.kind is not MarkdownNodeKind.SECTION
        and node.text.strip()
        and node.source_spans
        and start_offset <= node.start
        and node.end <= end_offset
    ]


def _section_id(kind: str, start_offset: int, end_offset: int) -> str:
    """基于类型与区间生成稳定 section ID"""
    identity = f"{kind}\0{start_offset}\0{end_offset}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"sec_{digest[:16]}"
