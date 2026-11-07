from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

import semchunk

from ..parsing.parser import MarkdownNode, MarkdownNodeKind

if TYPE_CHECKING:
    from .chunker import ChunkingPolicy


class StructuralNodeSplitter:
    """只拆超过阈值的节点；优先完整结构，无法安全拆分时保留原单元。"""

    def __init__(self, policy: ChunkingPolicy) -> None:
        """保存分块策略，并缓存其 token 计数器。"""
        self._policy = policy
        self._counter = policy.token_counter

    def split(self, node: MarkdownNode) -> list[MarkdownNode]:
        """按节点结构选择拆分策略；无需拆分或不安全时原样返回。"""
        # 硬阈值以内优先保留完整节点；只有超过硬阈值才按软目标尝试拆分。
        if self._counter.count(node.text) <= self._policy.split_threshold_tokens:
            return [node]

        # 段落和引用按语义文本拆分。
        if node.kind in {MarkdownNodeKind.PARAGRAPH, MarkdownNodeKind.QUOTE}:
            return self._split_text(node)

        # 列表、表格、代码分别按结构边界拆分。
        if node.kind is MarkdownNodeKind.LIST:
            return self._split_list(node)
        if node.kind is MarkdownNodeKind.TABLE:
            return self._split_table(node)
        if node.kind is MarkdownNodeKind.CODE:
            return self._split_code(node)

        # Formula、Figure 及未定义安全边界的结构保持原子性；最终由 Packer 判断 Overflow。
        return [node]

    def _split_text(self, node: MarkdownNode) -> list[MarkdownNode]:
        """将文本节点按语义片段切分为多个 part 节点。"""
        return [
            replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text=text,
                children=[],
                source_spans=[],
                metadata={**node.metadata, "source_node_ids": node.source_node_ids},
            )
            for index, text in enumerate(self._semantic_parts(node.text))
        ]

    def _semantic_parts(self, text: str) -> list[str]:
        """用 semchunk 做语义切分；残余超限片段交给 tokenizer 兜底。"""
        parts = semchunk.chunk(
            text,
            chunk_size=self._policy.target_chunk_tokens,
            token_counter=self._counter.count,
            overlap=None,  # 不请求 overlap，避免额外文本合成。
        )

        result: list[str] = []
        for part in parts:
            # 跳过空白片段，避免产生无意义分块。
            if not part.strip():
                continue

            # semchunk 可能返回超限片段，按 token 上限强制再切。
            if self._counter.count(part) > self._policy.target_chunk_tokens:
                result.extend(
                    self._counter.split(part, self._policy.target_chunk_tokens)
                )
            else:
                result.append(part)

        return result

    def _split_list(self, node: MarkdownNode) -> list[MarkdownNode]:
        """按列表项聚合分组，尽量保留列表结构；超阈值单项单独成组。"""
        items = [
            child for child in node.children if child.kind is MarkdownNodeKind.LIST_ITEM
        ]
        if not items:
            return [node]

        groups: list[list[MarkdownNode]] = []
        current: list[MarkdownNode] = []

        for item in items:
            current_text = "\n".join(part.text for part in current)
            candidate = "\n".join([*(part.text for part in current), item.text])

            # 超硬阈值的单项独立保留；其余仅在追加后距软目标不增时合并。
            if current and (
                self._counter.count(item.text) > self._policy.split_threshold_tokens
                or not self._policy.should_append(
                    current_tokens=self._counter.count(current_text),
                    candidate_tokens=self._counter.count(candidate),
                )
            ):
                groups.append(current)
                current = []

            current.append(item)

            # 无法安全拆分的超硬阈值单项立即独立输出，最终由 Packer 标记 Overflow。
            if self._counter.count(item.text) > self._policy.split_threshold_tokens:
                groups.append(current)
                current = []

        if current:
            groups.append(current)

        return [
            replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text="\n".join(item.text for item in group),
                children=group,
                source_spans=list(
                    dict.fromkeys(span for item in group for span in item.source_spans)
                ),
                metadata={
                    **node.metadata,
                    "source_node_ids": [
                        node.node_id,
                        *(
                            identity
                            for item in group
                            for identity in item.source_node_ids
                        ),
                    ],
                },
            )
            for index, group in enumerate(groups)
        ]

    def _split_table(self, node: MarkdownNode) -> list[MarkdownNode]:
        """按表格行分组，保留表头和 caption；缺少可靠行边界时原样返回。"""
        header = next(
            (
                child
                for child in node.children
                if child.kind is MarkdownNodeKind.TABLE_HEADER
            ),
            None,
        )
        rows = [
            child for child in node.children if child.kind is MarkdownNodeKind.TABLE_ROW
        ]
        header_text = node.metadata.get("table_header_text")

        # HTML table 等没有可靠行边界的结构不能退回纯文本拆分。
        if header is None or not rows or not header_text:
            return [node]

        caption = next(
            (
                child
                for child in node.children
                if child.kind is MarkdownNodeKind.PARAGRAPH
            ),
            None,
        )
        # 判断 caption 是否在表格上方
        caption_before = bool(
            caption and node.text.lstrip().startswith(caption.text.lstrip())
        )

        def build_group(group: list[MarkdownNode], index: int) -> MarkdownNode:
            """构造包含表头、当前行组及可选 caption 的表格分块。"""
            # 首组始终含至少一行；caption 与首行整体超限时也不拆 caption。
            children = [header, *group]
            texts = [str(header_text), *(row.text.rstrip("\r\n") for row in group)]

            if caption is not None and index == 0:
                if caption_before:
                    texts.insert(0, caption.text.rstrip("\r\n"))
                    children.insert(0, caption)
                else:
                    texts.append(caption.text.rstrip("\r\n"))
                    children.append(caption)

            return replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text="\n".join(texts),
                children=children,
                source_spans=list(
                    dict.fromkeys(
                        span for child in children for span in child.source_spans
                    )
                ),
                metadata={
                    **node.metadata,
                    "source_node_ids": [
                        node.node_id,
                        *(
                            identity
                            for child in children
                            for identity in child.source_node_ids
                        ),
                    ],
                },
            )

        parts: list[MarkdownNode] = []
        current: list[MarkdownNode] = []

        for row in rows:
            candidate = build_group([*current, row], len(parts))

            # 追加后的完整表格含重复表头和 caption；以实际文本距软目标的距离决策。
            if current and (
                self._counter.count(row.text) > self._policy.split_threshold_tokens
                or not self._policy.should_append(
                    current_tokens=self._counter.count(
                        build_group(current, len(parts)).text
                    ),
                    candidate_tokens=self._counter.count(candidate.text),
                )
            ):
                parts.append(build_group(current, len(parts)))
                current = []

            current.append(row)

            # 表头、caption 与单行无法安全拆开；整体超硬阈值时独立输出。
            if (
                self._counter.count(build_group(current, len(parts)).text)
                > self._policy.split_threshold_tokens
            ):
                parts.append(build_group(current, len(parts)))
                current = []

        if current:
            parts.append(build_group(current, len(parts)))

        return parts

    def _split_code(self, node: MarkdownNode) -> list[MarkdownNode]:
        """按完整原文代码行分组，并为每个 fenced 片段重建完整围栏。"""
        # 只按 LF 分行，避免把 U+2028 或其他代码字符误认为 Markdown 换行。
        lines = node.text.split("\n")
        lines = [line + "\n" for line in lines[:-1]] + (
            [lines[-1]] if lines[-1] else []
        )

        opening, closing = "", ""

        if node.metadata.get("fenced"):
            markup = str(node.metadata.get("markup") or "")

            # 首行必须是合法围栏；否则无法安全重建，保持原节点。
            if (
                not lines
                or not markup
                or not re.fullmatch(
                    r" {0,3}" + re.escape(markup) + r"[^\r\n]*\r?\n",
                    lines[0],
                )
            ):
                return [node]

            opening = lines.pop(0)
            newline = "\r\n" if opening.endswith("\r\n") else "\n"
            closing_pattern = (
                r" {0,3}"
                + re.escape(markup[0])
                + "{"
                + str(len(markup))
                + r",}[ \t]*\r?\n?"
            )

            # 有合法结束围栏则复用；未闭合代码补一个合成结束围栏。
            if lines and re.fullmatch(closing_pattern, lines[-1]):
                closing = lines.pop()
            else:
                closing = markup + newline

        if not lines:
            return [node]

        def render(body: list[str]) -> str:
            """把代码体重新包进围栏，返回分片文本。"""
            text = "".join(body)
            if closing and not text.endswith("\n"):
                # 未闭合原文可能没有末尾换行；此换行与重建围栏都属于合成文本。
                text += "\r\n" if opening.endswith("\r\n") else "\n"
            return opening + text + closing

        bodies: list[list[str]] = []
        current: list[str] = []

        for line in lines:
            # 围栏计入预算；完整行的组合以距软目标不增为准，不以硬阈值填满。
            if current and (
                self._counter.count(render([line]))
                > self._policy.split_threshold_tokens
                or not self._policy.should_append(
                    current_tokens=self._counter.count(render(current)),
                    candidate_tokens=self._counter.count(render([*current, line])),
                )
            ):
                bodies.append(current)
                current = []

            current.append(line)

            # 超硬阈值的代码行仍保持完整，独立输出后由 Packer 标记 Overflow。
            if (
                self._counter.count(render(current))
                > self._policy.split_threshold_tokens
            ):
                bodies.append(current)
                current = []

        if current:
            bodies.append(current)

        return [
            replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text=render(body),
                children=[],
                # 不把重复围栏伪装成原文位置，也不通过 find() 反推 body offset。
                source_spans=[],
                metadata={**node.metadata, "source_node_ids": node.source_node_ids},
            )
            for index, body in enumerate(bodies)
        ]
