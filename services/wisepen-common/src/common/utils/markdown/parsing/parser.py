from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from .plugins import standalone_figure_plugin


class MarkdownNodeKind(StrEnum):
    """会影响 Markdown 分块边界的节点类型。"""

    SECTION = "section"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    TABLE = "table"
    TABLE_HEADER = "table_header"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    CODE = "code"
    FORMULA = "formula"
    FIGURE = "figure"
    INLINE = "inline"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """原文中的 Python 字符半开区间。"""

    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("source span must satisfy 0 <= start_offset <= end_offset")

    @property
    def length(self) -> int:
        return self.end_offset - self.start_offset


@dataclass(frozen=True, slots=True)
class MarkdownNode:
    """Markdown 结构节点；source_spans 为可选来源范围。"""

    node_id: str
    kind: MarkdownNodeKind
    text: str
    source_spans: list[SourceSpan] = field(default_factory=list)
    children: list[MarkdownNode] = field(default_factory=list)
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def source_node_ids(self) -> list[str]:
        """结构来源归属，不保证与派生片段的文本范围精确对应。"""

        identities = self.metadata.get("source_node_ids")
        if identities is not None:
            return list(identities)
        return [
            self.node_id,
            *(identity for child in self.children for identity in child.source_node_ids),
        ]

    @property
    def start(self) -> int | None:
        return min((span.start_offset for span in self.source_spans), default=None)

    @property
    def end(self) -> int | None:
        return max((span.end_offset for span in self.source_spans), default=None)


NUMBERED_LABEL_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_~\s]*"
    r"(?:(?P<table_label>Table|表格|表)|(?P<figure_label>Figure|Fig\.?|图))"
    r"\s*(?P<number>\d+(?:\.\d+)*)\s*[-:：.．、]\s*"
    r"(?P<title>\S(?:.*\S)?)[*_~\s]*$",
    re.IGNORECASE | re.DOTALL,
)
FORMULA_LABEL_RE = re.compile(
    r"(?:Equation|Eq\.?|公式)\s+\(?(?P<number>\d+(?:\.\d+)*)\)?",
    re.IGNORECASE,
)

# None 表示分组 token，只保留其内部节点。
_OPEN_TOKEN_KINDS: dict[str, MarkdownNodeKind | None] = {
    "heading_open": MarkdownNodeKind.SECTION,
    "paragraph_open": MarkdownNodeKind.PARAGRAPH,
    "blockquote_open": MarkdownNodeKind.QUOTE,
    "bullet_list_open": MarkdownNodeKind.LIST,
    "ordered_list_open": MarkdownNodeKind.LIST,
    "list_item_open": MarkdownNodeKind.LIST_ITEM,
    "table_open": MarkdownNodeKind.TABLE,
    "thead_open": MarkdownNodeKind.TABLE_HEADER,
    "tbody_open": None,
    "tr_open": MarkdownNodeKind.TABLE_ROW,
    "th_open": MarkdownNodeKind.TABLE_CELL,
    "td_open": MarkdownNodeKind.TABLE_CELL,
    "figure_open": MarkdownNodeKind.FIGURE,
}


class MarkdownParser:
    """将 markdown-it token 转换为带来源范围的结构节点树。"""

    def __init__(self) -> None:
        self._parser = (
            MarkdownIt("commonmark")
            .disable("lheading")
            .enable("table")
            .use(standalone_figure_plugin)
            .use(dollarmath_plugin)
        )

    def parse(self, text: str) -> list[MarkdownNode]:
        if not text:
            return []

        nodes = _MarkdownTokenTreeBuilder(
            tokens=self._parser.parse(text),
            text=text,
            line_offsets=_build_line_offsets(text),
        ).build()
        normalized = _normalize_markdown_nodes(nodes, text)
        if normalized:
            # 在所有结构变换结束后统一分配路径 ID。
            return [
                _reassign_node_ids(node, f"node-{index}")
                for index, node in enumerate(normalized)
            ]

        if text.strip():
            return [
                MarkdownNode(
                    node_id="node-0",
                    kind=MarkdownNodeKind.PARAGRAPH,
                    text=text,
                    source_spans=[SourceSpan(0, len(text))],
                    metadata={"source_format": "raw"},
                ),
            ]
        return []


def _reassign_node_ids(node: MarkdownNode, node_id: str) -> MarkdownNode:
    """按树路径重新编号，包括归一化后重新挂接的子节点。"""

    return replace(
        node,
        node_id=node_id,
        children=[
            _reassign_node_ids(child, f"{node_id}:{index}")
            for index, child in enumerate(node.children)
        ],
    )


class _MarkdownTokenTreeBuilder:
    """递归还原 markdown-it 的扁平 token 序列。"""

    def __init__(
        self,
        *,
        tokens: list[Token],
        text: str,
        line_offsets: list[int],
    ) -> None:
        self._tokens = tokens
        self._text = text
        self._line_offsets = line_offsets
        self._next_id = 0

    def build(self) -> list[MarkdownNode]:
        nodes, _ = self._parse_until_close(0, None)
        return nodes

    def _parse_until_close(
        self,
        index: int,
        closing_type: str | None,
    ) -> tuple[list[MarkdownNode], int]:
        """消费 token，直到遇到对应关闭 token 或序列结束。"""

        nodes: list[MarkdownNode] = []
        while index < len(self._tokens):
            token = self._tokens[index]
            if closing_type is not None and token.type == closing_type:
                return nodes, index + 1

            if token.nesting == 1:
                kind = _OPEN_TOKEN_KINDS.get(token.type)
                children, index = self._parse_until_close(
                    index + 1,
                    token.type.replace("_open", "_close"),
                )
                if kind is None:
                    nodes.extend(children)
                    continue

                spans = _resolve_token_source_spans(token, self._line_offsets, children)
                node_text = _resolve_node_text(self._text, spans, children)
                metadata = _extract_node_metadata(token, kind)
                if kind is MarkdownNodeKind.TABLE:
                    # 表格分隔行不单独成 token，在此保留原始表头两行。
                    metadata["table_header_text"] = "\n".join(
                        node_text.split("\n")[:2]
                    ).strip()
                nodes.append(
                    self._new_node(
                        kind=kind,
                        text=node_text,
                        source_spans=spans,
                        children=children,
                        metadata=metadata,
                    )
                )
                continue

            if token.nesting == 0 and (
                token.map is not None
                or token.type in {"fence", "math_block", "math_block_label"}
            ):
                node = self._build_leaf_node(token)
                if node is not None:
                    nodes.append(node)
            index += 1

        return nodes, index

    def _build_leaf_node(self, token: Token) -> MarkdownNode | None:
        """把可保留的叶子 token 转为节点。"""

        if token.type == "fence":
            kind = MarkdownNodeKind.CODE
            info = token.info.strip()
            metadata = {
                "language": info.split(maxsplit=1)[0] if info else None,
                "info": token.info,
                "markup": token.markup,
                "fenced": True,
            }
        elif token.type == "code_block":
            kind = MarkdownNodeKind.CODE
            metadata = {"language": None, "fenced": False}
        elif token.type in {"math_block", "math_block_label"}:
            kind = MarkdownNodeKind.FORMULA
            metadata = {}
        elif token.type == "html_block":
            if not token.content.lstrip().lower().startswith("<table"):
                return None
            kind = MarkdownNodeKind.TABLE
            metadata = {"source_format": "html"}
        elif token.type == "inline":
            return self._build_inline_node(token)
        else:
            return None

        spans = _resolve_token_source_spans(token, self._line_offsets, [])
        return self._new_node(
            kind=kind,
            text=_resolve_node_text(self._text, spans, []),
            source_spans=spans,
            metadata=metadata,
        )

    def _build_inline_node(self, token: Token) -> MarkdownNode:
        """父节点保留整段行内内容，子节点记录 inline token 类型。"""

        children = [
            self._new_node(
                kind=MarkdownNodeKind.INLINE,
                text=child.content,
                source_spans=[],
                metadata={
                    "token_type": child.type,
                    "attrs": child.attrs,
                    "markup": child.markup,
                },
            )
            for child in token.children or ()
        ]
        return self._new_node(
            kind=MarkdownNodeKind.INLINE,
            text=token.content,
            source_spans=_resolve_token_source_spans(token, self._line_offsets, []),
            children=children,
            metadata={"token_type": "inline"},
        )

    def _new_node(
        self,
        *,
        kind: MarkdownNodeKind,
        text: str,
        source_spans: list[SourceSpan],
        children: list[MarkdownNode] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MarkdownNode:
        node = MarkdownNode(
            node_id=f"node-{self._next_id}",
            kind=kind,
            text=text,
            source_spans=source_spans,
            children=children if children is not None else [],
            metadata=metadata if metadata is not None else {},
        )
        self._next_id += 1
        return node


def _build_line_offsets(text: str) -> list[int]:
    """按 LF 建立行起始偏移表，保留原文中的 CRLF。"""

    offsets = [0]
    for index, character in enumerate(text):
        if character == "\n":
            offsets.append(index + 1)
    if offsets[-1] != len(text):
        offsets.append(len(text))
    return offsets


def _resolve_token_source_spans(
    token: Token,
    line_offsets: list[int],
    children: list[MarkdownNode],
) -> list[SourceSpan]:
    """优先使用 token 行映射，否则使用子节点范围包络。"""

    # 单元格的行映射可能覆盖整行，不能当作单元格的精确来源。
    if token.type in {"th_open", "td_open"}:
        return []

    if token.map is not None:
        start_line, end_line = token.map
        return [SourceSpan(line_offsets[start_line], line_offsets[end_line])]

    child_spans = [
        span for child in children for span in child.source_spans if span.length
    ]
    if not child_spans:
        return []
    return [
        SourceSpan(
            min(span.start_offset for span in child_spans),
            max(span.end_offset for span in child_spans),
        ),
    ]


def _resolve_node_text(
    source: str,
    spans: list[SourceSpan],
    children: list[MarkdownNode],
) -> str:
    """优先切取原文；无来源范围时拼接子节点文本。"""

    if spans:
        return source[spans[0].start_offset : spans[-1].end_offset]
    return "\n".join(child.text for child in children if child.text)


def _extract_node_metadata(token: Token, kind: MarkdownNodeKind) -> dict[str, object]:
    """提取列表、标题及表格单元格所需的 token 属性。"""

    metadata: dict[str, object] = {}
    if kind is MarkdownNodeKind.LIST:
        metadata["ordered"] = token.type == "ordered_list_open"
        if token.attrs and "start" in token.attrs:
            metadata["start"] = token.attrs["start"]
    if kind is MarkdownNodeKind.SECTION:
        metadata["heading_level"] = int(token.tag[1])
    if kind is MarkdownNodeKind.TABLE_CELL:
        metadata["alignment"] = dict(token.attrs or {}).get("style")
    return metadata


def _normalize_markdown_nodes(
    nodes: list[MarkdownNode],
    source: str,
) -> list[MarkdownNode]:
    """处理章节路径、重复标题、编号题注和空标题。"""

    normalized: list[MarkdownNode] = []
    headings: list[tuple[int, str]] = []
    previous_heading: tuple[int, list[str], str] | None = None

    for node in nodes:
        if node.kind is MarkdownNodeKind.SECTION:
            inline_heading = next(
                (child for child in node.children if child.kind is MarkdownNodeKind.INLINE),
                None,
            )
            title = (inline_heading.text if inline_heading else node.text).strip()
            if not title:
                continue
            level = int(node.metadata["heading_level"])

            parent_index = next(
                (
                    index
                    for index, (heading_level, _) in enumerate(headings)
                    if heading_level >= level
                ),
                len(headings),
            )
            parent_path = [title for _, title in headings[:parent_index]]

            # 只跳过真正相邻、同层且同父路径的重复标题。
            if previous_heading == (level, parent_path, title):
                continue

            headings = headings[:parent_index]
            headings.append((level, title))
            previous_heading = (level, parent_path, title)
            normalized.append(
                replace(
                    node,
                    metadata={
                        **node.metadata,
                        "title": title,
                        "section_path": [title for _, title in headings],
                    },
                )
            )
            continue

        previous_heading = None
        metadata = {
            **node.metadata,
            "section_path": [title for _, title in headings],
        }
        if node.kind is MarkdownNodeKind.FORMULA:
            formula_match = FORMULA_LABEL_RE.search(node.text)
            if formula_match is not None:
                metadata["anchor_label"] = f"Equation {formula_match.group('number')}"
        normalized.append(replace(node, metadata=metadata))

    return _prune_empty_headings(_attach_numbered_captions(normalized, source))


def _prune_empty_headings(nodes: list[MarkdownNode]) -> list[MarkdownNode]:
    """删除没有正文和直接下级标题的空标题，随后重建章节路径。"""

    headings_to_remove: set[int] = set()
    for index, node in enumerate(nodes):
        if node.kind is not MarkdownNodeKind.SECTION:
            continue
        level = int(node.metadata["heading_level"])
        has_content = False
        has_child = False

        for following in nodes[index + 1 :]:
            if following.kind is MarkdownNodeKind.SECTION:
                has_child = int(following.metadata["heading_level"]) > level
                break
            if following.text.strip():
                has_content = True
                break
        if not has_content and not has_child:
            headings_to_remove.add(index)

    if not headings_to_remove:
        return nodes

    result: list[MarkdownNode] = []
    headings: list[tuple[int, str]] = []
    for index, node in enumerate(nodes):
        if index in headings_to_remove:
            continue
        if node.kind is MarkdownNodeKind.SECTION:
            level = int(node.metadata["heading_level"])
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, str(node.metadata["title"])))
        result.append(
            replace(
                node,
                metadata={
                    **node.metadata,
                    "section_path": [title for _, title in headings],
                },
            )
        )
    return result


def _attach_numbered_captions(
    nodes: list[MarkdownNode],
    source: str,
) -> list[MarkdownNode]:
    """把相邻的编号题注段落挂接到表格或图片节点。"""

    result: list[MarkdownNode] = []
    index = 0
    while index < len(nodes):
        first = nodes[index]
        if index + 1 < len(nodes):
            second = nodes[index + 1]
            if first.kind is MarkdownNodeKind.PARAGRAPH:
                caption, target = first, second
            else:
                caption, target = second, first

            label = (
                _parse_numbered_caption(caption.text)
                if caption.kind is MarkdownNodeKind.PARAGRAPH
                else None
            )
            target_kind = (
                MarkdownNodeKind.TABLE
                if label and label[0] == "table"
                else MarkdownNodeKind.FIGURE
                if label
                else None
            )
            if (
                label is not None
                and target.kind is target_kind
                and _has_only_whitespace_between(source, first, second)
            ):
                start = min(span.start_offset for span in first.source_spans)
                end = max(span.end_offset for span in second.source_spans)
                result.append(
                    replace(
                        target,
                        text=source[start:end],
                        source_spans=[SourceSpan(start, end)],
                        children=[*target.children, caption],
                        metadata={
                            **target.metadata,
                            "anchor_label": label[1],
                            "caption": caption.text,
                            "figure_text": target.text,
                        },
                    )
                )
                index += 2
                continue

        result.append(first)
        index += 1
    return result


def _parse_numbered_caption(text: str) -> tuple[str, str] | None:
    """解析编号题注，返回 (table/figure, 规范化锚点)。"""

    match = NUMBERED_LABEL_RE.fullmatch(text.strip())
    if match is None:
        return None
    number = match.group("number")
    if match.group("table_label") is not None:
        return "table", f"Table {number}"
    return "figure", f"Figure {number}"


def _has_only_whitespace_between(
    source: str,
    first: MarkdownNode,
    second: MarkdownNode,
) -> bool:
    """判断按文档顺序排列的两个节点之间是否仅有空白。"""

    if not first.source_spans or not second.source_spans:
        return False
    first_end = max(span.end_offset for span in first.source_spans)
    second_start = min(span.start_offset for span in second.source_spans)
    if first_end > second_start:
        return False
    return not source[first_end:second_start].strip()
