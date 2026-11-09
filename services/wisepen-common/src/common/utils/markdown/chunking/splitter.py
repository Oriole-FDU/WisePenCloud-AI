"""超阈值节点的结构拆分

仅拆分超过硬阈值的节点，优先保留完整结构，无法安全拆分时保留原单元
- PARAGRAPH / QUOTE：按语义切分，残余超限片段由 tokenizer 兜底；
- LIST：按列表项分组；
- TABLE：按行分组，每组保留表头与题注；
- CODE：按行分组，fenced 片段重建完整围栏；
- FORMULA / FIGURE 及其他：原子保留，不拆分。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

import semchunk

from ..model import MarkdownNodeKind, MarkdownNode

if TYPE_CHECKING:
    from .chunker import ChunkingPolicy


class StructuralNodeSplitter:
    """只拆超过阈值的节点；优先完整结构，无法安全拆分时保留原单元"""

    def __init__(self, policy: ChunkingPolicy) -> None:
        self._policy = policy
        self._counter = policy.token_counter

    def split(self, node: MarkdownNode) -> list[MarkdownNode]:
        """按节点结构选择拆分策略，无需拆分或不安全时原样返回"""
        # 硬阈值以内优先保留完整节点，只有超过硬阈值才按软目标尝试拆分
        if self._counter.count(node.text) <= self._policy.split_threshold_tokens:
            return [node]

        # 段落和引用按语义文本拆分
        if node.kind in {MarkdownNodeKind.PARAGRAPH, MarkdownNodeKind.QUOTE}:
            return self._split_text(node)
        # 列表、表格、代码分别按结构边界拆分
        if node.kind is MarkdownNodeKind.LIST:
            return self._split_list(node)
        if node.kind is MarkdownNodeKind.TABLE:
            return self._split_table(node)
        if node.kind is MarkdownNodeKind.CODE:
            return self._split_code(node)

        # Formula、Figure 及未定义安全边界的结构保持原子性
        # 最终由 Packer 判断 Overflow
        return [node]

    def _split_text(self, node: MarkdownNode) -> list[MarkdownNode]:
        """将文本节点按语义片段切分为多个 part 节点"""

        # 先用 semchunk 按语义切分；残余超限片段再交给 tokenizer 兜底
        parts = semchunk.chunk(
            node.text,
            chunk_size=self._policy.target_chunk_tokens,  # 切分目标
            token_counter=self._counter.count,  # 计数器
            overlap=None,  # 不请求 overlap
        )

        fragments: list[str] = []
        for part in parts:
            # 空白片段丢弃
            if not part.strip():
                continue

            # semchunk 只是尽力不超过目标，但做不到保证
            # 用二分逼近找到不超预算的切点，按 Unicode 字符边界强制切分
            if self._counter.count(part) > self._policy.target_chunk_tokens:
                fragments.extend(
                    self._counter.split(part, self._policy.target_chunk_tokens)
                )
            else:
                fragments.append(part)

        # 把每个片段包成 part 节点
        return [
            replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text=text,
                children=[],
                source_spans=[],
                metadata={**node.metadata, "source_node_ids": node.source_node_ids},
            )
            for index, text in enumerate(fragments)
        ]

    def _split_list(self, node: MarkdownNode) -> list[MarkdownNode]:
        """将列表节点按列表项分组切分为多个 part 节点，超阈值单项单独成组"""

        # 取出列表项子节点
        items = [
            child for child in node.children if child.kind is MarkdownNodeKind.LIST_ITEM
        ]
        if not items:
            return [node]

        groups: list[list[MarkdownNode]] = [] # 最终的分组结果
        current: list[MarkdownNode] = [] # 正在攒的当前组（若干连续列表项）

        for item in items:
            current_text = "\n".join(part.text for part in current)
            candidate = "\n".join([*(part.text for part in current), item.text])

            # 如果当前组非空，且满足下面任一条件，就先把 current 封存成一个组
            # 1. 这个 item 自己超硬阈值，无法跟其他组合并
            # 2. should_append 返回不该追加，把 item 并进来会让 token 数离软目标更远
            if current and (
                self._counter.count(item.text) > self._policy.split_threshold_tokens
                or not self._policy.should_append(
                    current_tokens=self._counter.count(current_text),
                    candidate_tokens=self._counter.count(candidate),
                )
            ):
                groups.append(current)
                current = []

            current.append(item) # 装进当前组

            # 如果刚装进来的这个 item 自己超硬阈值，就立刻把它单独封成一个组
            if self._counter.count(item.text) > self._policy.split_threshold_tokens:
                groups.append(current)
                current = []

        if current:
            groups.append(current)

        # 把每组包成 part 节点
        return [
            replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text="\n".join(item.text for item in group),
                children=group, # 保留列表项子节点
                source_spans=list(
                    dict.fromkeys(span for item in group for span in item.source_spans)
                    # 列表项没被重切，原文区间是真实的，保留
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
        """将表格节点按行分组切分为多个 part 节点，每个 part 都保留表头与 caption"""

        # 定位表头、数据行与表头文本
        header = next( # 表头子节点（TABLE_HEADER）
            (
                child
                for child in node.children
                if child.kind is MarkdownNodeKind.TABLE_HEADER
            ),
            None,
        )
        rows = [
            child for child in node.children if child.kind is MarkdownNodeKind.TABLE_ROW # 所有数据行
        ]
        header_text = node.metadata.get("table_header_text") # 表头行 + 分隔行的原文

        # HTML 表格这类没有可靠行边界、或拿不到表头文本的结构，直接原样返回
        if header is None or not rows or not header_text:
            return [node]

        # 挂接在表格节点上的编号题注段落，可能没有
        caption = next(
            (
                child
                for child in node.children
                if child.kind is MarkdownNodeKind.PARAGRAPH
            ),
            None,
        )
        # 判断 caption 在原文里位于表格上方还是下方
        caption_before = bool(
            caption and node.text.lstrip().startswith(caption.text.lstrip())
        )

        def build_group(group: list[MarkdownNode], index: int) -> MarkdownNode:
            """构造包含表头、当前行组及可选 caption 的表格分块"""
            children = [header, *group]  # 表头始终在最前，后面跟当前行组
            texts = [str(header_text), *(row.text.rstrip("\r\n") for row in group)]

            # caption 只随首组出现，按原文位置放到表头之前或之后
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
                children=children,  # 保留表头、caption 与数据行子节点
                source_spans=list(
                    dict.fromkeys(
                        span for child in children for span in child.source_spans
                    )
                    # 行组未被重切，原文区间真实，保留
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

        parts: list[MarkdownNode] = []  # 最终的行组 part 节点
        current: list[MarkdownNode] = []  # 正在攒的当前行组

        for row in rows:
            candidate = build_group([*current, row], len(parts))

            # 如果当前组非空，且满足下面任一条件，就先把 current 封存成一个组
            # 1. 这个 row 自己超硬阈值，无法跟其他组合并
            # 2. should_append 返回不该追加，把 row 并进来会让 token 数离软目标更远
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

            current.append(row)  # 装进当前组

            # 如果刚装进来的这个 item 自己超硬阈值，就立刻把它单独封成一个组
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
        """将代码节点按行分组切分为多个 part 节点，fenced 片段重建完整围栏"""

        # 只按 LF 分行，避免把 U+2028 或其他代码字符误认为 Markdown 换行
        lines = node.text.split("\n")
        # split("\n") 会把换行符吃掉，所以除了最后一行每行都补回一个 \n
        lines = [line + "\n" for line in lines[:-1]] + (
            [lines[-1]] if lines[-1] else []
        )

        opening, closing = "", ""  # 起始、结束围栏

        if node.metadata.get("fenced"):
            markup = str(node.metadata.get("markup") or "")  # 围栏标记

            # 先校验首行是不是合法围栏
            # 首行必须长得像 ```python 这种（0~3 个空格 + 围栏标记 + 可选语言信息 + 换行）
            # 缺少行、缺少围栏标记，或首行不是合法围栏，都无法安全重建，原样返回
            if (
                not lines
                or not markup
                or not re.fullmatch(
                    r" {0,3}" + re.escape(markup) + r"[^\r\n]*\r?\n",
                    lines[0],
                )
            ):
                return [node]

            opening = lines.pop(0)  # 弹出起始围栏
            newline = "\r\n" if opening.endswith("\r\n") else "\n"  # 按围栏行尾判断换行符
            # 结束围栏的正则
            closing_pattern = (
                r" {0,3}"
                + re.escape(markup[0])
                + "{"
                + str(len(markup))
                + r",}[ \t]*\r?\n?"
            )

            # 有合法结束围栏就复用原文
            if lines and re.fullmatch(closing_pattern, lines[-1]):
                closing = lines.pop()
            else: # 如果原代码块没写结束围栏（未闭合），就合成一个补上，保证每个 fragment 都能独立渲染
                closing = markup + newline

        if not lines:
            return [node]  # 只有围栏没有代码体，无法拆分，原样返回

        def render(body: list[str]) -> str:
            """把代码体重新包进围栏，返回分片文本"""
            text = "".join(body)  # 拼回代码体
            if closing and not text.endswith("\n"):
                # 未闭合原文可能没有末尾换行，补一个
                # 此换行与重建围栏都属于合成文本
                text += "\r\n" if opening.endswith("\r\n") else "\n"
            return opening + text + closing

        bodies: list[list[str]] = []  # 最终的行组（每个是若干代码行）
        current: list[str] = []  # 正在攒的当前行组

        for line in lines:

            # 如果当前组非空，且满足下面任一条件，就先把 current 封存成一个组
            # 1. 这个 line 自己超硬阈值，无法跟其他组合并
            # 2. should_append 返回不该追加，把 line 并进来会让 token 数离软目标更远
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

            current.append(line)  # 装进当前组

            # 如果刚装进来的这个 line 自己超硬阈值，就立刻把它单独封成一个组
            if (
                self._counter.count(render(current))
                > self._policy.split_threshold_tokens
            ):
                bodies.append(current)
                current = []

        if current:
            bodies.append(current)

        # 把每组包成 part 节点
        return [
            replace(
                node,
                node_id=f"{node.node_id}:part:{index}",
                text=render(body),
                children=[],
                source_spans=[],
                metadata={**node.metadata, "source_node_ids": node.source_node_ids},
            )
            for index, body in enumerate(bodies)
        ]
