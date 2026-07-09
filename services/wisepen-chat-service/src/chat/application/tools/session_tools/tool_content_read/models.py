from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ToolContentSelector:
    """读取前置候选域过滤器。

    所有条件同时存在时取交集（AND 逻辑）；为空则表示不过滤。
    - block_kinds: 按块内结构类型过滤（如 code/table/formula）
    - sections/page_labels/anchor_labels: 按结构化索引值过滤
    - chunk_indices: 显式指定 chunk 序号（精准定位）
    """

    block_kinds: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    page_labels: tuple[str, ...] = ()
    anchor_labels: tuple[str, ...] = ()
    chunk_indices: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentRerankReadRequest:
    """rerank 读取内部请求。"""

    content_ids: tuple[str, ...]
    query: str
    selector: ToolContentSelector | None = None
    top_k: int = 10
    merge_before: int = 0
    merge_after: int = 0


@dataclass(frozen=True, slots=True)
class ToolContentRegexReadRequest:
    """regex 读取内部请求。"""

    content_ids: tuple[str, ...]
    pattern: str
    selector: ToolContentSelector | None = None
    max_matches: int = 10
    merge_before: int = 0
    merge_after: int = 0


@dataclass(frozen=True, slots=True)
class ToolContentWindow:
    """一次读取产生的模型上下文窗口。

    包含文本内容及其在原文中的定位信息（offset、chunk 范围）。
    ranked_expand 模式额外携带 rank/score，regex_match 模式携带 match_text。
    """

    text: str  # 窗口文本内容
    start_offset: int | None = None  # 在原文中的起始字符偏移
    end_offset: int | None = None  # 在原文中的结束字符偏移
    center_chunk: int | None = None  # 中心 chunk 序号
    chunk_start: int | None = None  # 窗口起始 chunk 序号
    chunk_end: int | None = None  # 窗口结束 chunk 序号
    page_label: str | None = None  # 页码标签
    section_title: str | None = None  # section_path 最末级标题
    section_path: tuple[str, ...] = ()  # 小节路径
    anchor_labels: tuple[str, ...] = ()  # 锚点标签


@dataclass(frozen=True, slots=True)
class ToolContentReadMatch:
    """跨文档读取后的单条全局命中结果。"""

    content_id: str  # 实际读取的内容 ID（可能经过重定向）
    window: ToolContentWindow | None = None  # 匹配到的窗口
    reason: str | None = None  # 单项失败原因


@dataclass(frozen=True, slots=True)
class ToolContentReadResult:
    """session 内容读取工具的全局有序结果。"""

    matches: tuple[ToolContentReadMatch, ...] = ()
    failed: tuple[ToolContentReadMatch, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentSequentialReadResult:
    """单文档顺序读取结果。"""

    content_id: str
    status: str
    window: ToolContentWindow | None = None
    reason: str | None = None
