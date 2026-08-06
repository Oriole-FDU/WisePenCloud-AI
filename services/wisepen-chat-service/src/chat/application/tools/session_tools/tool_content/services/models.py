from __future__ import annotations

from dataclasses import dataclass, field

from chat.application.utils.chunkers import SourceSpan


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotPage:
    """描述正文中的一个 page 入口，不携带正文内容。"""

    page_label: str  # 供后续 read_pages 精确请求的页标签。
    start_offset: int  # 原始正文中的 Python 字符起点，含义不是 token 起点。
    end_offset: int  # 原始正文中的 Python 字符终点，遵循半开区间 [start, end)。


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotSection:
    """描述 section 树中的一个节点，不携带正文内容。"""

    title: str  # 当前层级的标题，不包含父级路径。
    section_path: str  # 从根节点到当前节点的稳定路径。
    start_offset: int  # 原始正文字符偏移，供 read_sections 使用。
    end_offset: int  # 原始正文字符偏移，遵循半开区间 [start, end)。
    has_content: bool  # 标题节点是否拥有可读正文，而不是是否存在子节点。
    children: tuple["ToolContentSnapshotSection", ...] = ()  # 按正文出现顺序排列。


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotAnchor:
    """描述附着在正文范围上的 anchor，不单独承担读取入口。"""

    anchor_label: str  # anchor 是上下文元数据，不是独立的 read 资源。
    start_offset: int  # anchor 在原文中的字符起点。
    end_offset: int  # anchor 在原文中的字符终点。


@dataclass(frozen=True, slots=True)
class ToolContentSemanticSearchRequest:
    """描述一次跨工具内容的语义检索请求。"""

    content_ids: tuple[str, ...]  # 搜索范围；每个 id 的加载失败独立返回。
    query: str  # 交给 ranking pipeline 的自然语言查询。
    top_k: int = 10  # 排名阶段最多保留的候选数，之后仍受输出字符总预算限制。


@dataclass(frozen=True, slots=True)
class ToolContentRegexSearchRequest:
    """描述一次基于原文字符位置的正则搜索请求。"""

    content_ids: tuple[str, ...]  # 搜索范围；单个内容失败不阻断其他内容。
    pattern: str  # 由 regex 库编译和执行的模式。
    max_matches: int = 10  # 匹配数量上限，不等于返回正文字数上限。
    context_chars: int | None = None  # 指定后按字符扩展，否则按默认单侧字符预算扩展。


@dataclass(frozen=True, slots=True)
class ToolContentWindow:
    """从权威原文读取出的、可直接交给模型的上下文窗口。

    `start_offset`、`end_offset` 和 `source_spans` 保留原文字符坐标；
    `text` 才是经过字符预算裁剪后实际返回给模型的内容。两套信息必须
    同时存在，因为调用方需要既能显示上下文，也能继续定位原文。
    """

    text: str  # 实际返回的模型可见文本，可能是原文的一部分。
    start_offset: int  # 覆盖范围的最小字符起点。
    end_offset: int  # 覆盖范围的最大字符终点，遵循半开区间。
    source_spans: tuple[SourceSpan, ...] = ()  # chunk 拼接时保留每个原文片段的坐标。
    page_labels: tuple[str, ...] = ()  # 与窗口字符范围相交的 page 标签。
    section_paths: tuple[str, ...] = ()  # 与窗口字符范围相交的 section 路径。
    anchor_labels: tuple[str, ...] = ()  # 与窗口字符范围相交的附属 anchor。
    truncated: bool = False  # True 表示原请求范围超过本窗口字符预算。
    metadata: dict[str, object] = field(default_factory=dict)  # 原始工具内容的来源元数据。


@dataclass(frozen=True, slots=True)
class ToolContentReadFailure:
    """记录批量读取中某个 content 独立失败的原因。"""

    content_id: str  # 失败的工具内容 id。
    reason: str  # 机器可读原因，例如 content_not_found 或异常类型名。


@dataclass(frozen=True, slots=True)
class ToolContentRegexSearchMatch:
    """描述一次 regex 命中及其周围的模型可见窗口。"""

    content_id: str  # 命中所在的工具内容。
    match_start: int  # 命中在完整原文中的字符起点。
    match_end: int  # 命中在完整原文中的字符终点。
    window: ToolContentWindow  # 包含命中和上下文的受预算保护窗口。


@dataclass(frozen=True, slots=True)
class ToolContentRegexSearchResult:
    """返回 regex 命中、逐内容失败和全局输出预算状态。"""

    matches: tuple[ToolContentRegexSearchMatch, ...] = ()  # 按搜索顺序返回。
    failed: tuple[ToolContentReadFailure, ...] = ()  # 不影响其他内容的局部失败。
    budget_exhausted: bool = False  # True 表示还有候选命中但总窗口预算已用尽。


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotResult:
    """返回结构导航信息和原文总长度，不读取正文窗口。"""

    content_id: str  # 请求的工具内容 id。
    content_type: str | None = None  # 权威存储声明的内容类型。
    total_length: int | None = None  # 完整原文的 Python 字符长度。
    pages: tuple[ToolContentSnapshotPage, ...] = ()  # 可按标签继续读取的 page。
    sections: tuple[ToolContentSnapshotSection, ...] = ()  # 可按路径继续读取的 section。
    anchors: tuple[ToolContentSnapshotAnchor, ...] = ()  # 附着在范围上的上下文标记。
    metadata: dict[str, object] = field(default_factory=dict)  # 来源和解析元数据。
    reason: str | None = None  # 仅在 snapshot 本身无法生成时使用。


@dataclass(frozen=True, slots=True)
class ToolContentSemanticSearchItem:
    """描述一个排序后的语义检索结果及其可读窗口。"""

    content_id: str  # 命中所在的工具内容。
    rank: int  # ranking pipeline 给出的名次。
    score: float  # ranking pipeline 给出的相关性分数。
    chunk_index: int  # 命中 chunk 在该内容中的稳定索引。
    window: ToolContentWindow  # 从命中 chunk 生成并受字符预算限制的窗口。


@dataclass(frozen=True, slots=True)
class ToolContentSemanticSearchResult:
    """返回语义检索结果、逐内容失败和全局窗口预算状态。"""

    results: tuple[ToolContentSemanticSearchItem, ...] = ()  # 按 ranking 顺序排列。
    failed: tuple[ToolContentReadFailure, ...] = ()  # 加载失败不抹掉已成功的结果。
    budget_exhausted: bool = False  # 排名结果未必全部能展开为正文窗口。


@dataclass(frozen=True, slots=True)
class ToolContentRangeReadResult:
    """返回一个字符范围对应的正文窗口，或返回读取失败原因。"""

    content_id: str  # 请求的工具内容 id。
    window: ToolContentWindow | None = None  # 内容存在时的唯一窗口。
    reason: str | None = None  # 例如 content_not_found。


@dataclass(frozen=True, slots=True)
class ToolContentPageReadItem:
    """返回一个 page 的窗口集合及其局部读取状态。"""

    page_label: str  # 请求中的 page 标签。
    windows: tuple[ToolContentWindow, ...] = ()  # 可包含预算截断后的部分窗口。
    reason: str | None = None  # page_not_found 或 page_budget_exhausted 等局部原因。


@dataclass(frozen=True, slots=True)
class ToolContentPageReadResult:
    """返回多个 page 的读取结果及共享总预算状态。"""

    content_id: str  # 实际读取的工具内容 id。
    items: tuple[ToolContentPageReadItem, ...] = ()  # 保持请求去重后的顺序。
    budget_exhausted: bool = False  # 后续 page 可能未读取，或当前 page 仅部分返回。


@dataclass(frozen=True, slots=True)
class ToolContentSectionReadItem:
    """返回一个 section path 的窗口集合及其局部读取状态。"""

    section_path: str  # 请求中的完整 section 路径。
    windows: tuple[ToolContentWindow, ...] = ()  # 可包含预算截断后的部分窗口。
    reason: str | None = None  # section_not_found 或 section_budget_exhausted 等原因。


@dataclass(frozen=True, slots=True)
class ToolContentSectionReadResult:
    """返回多个 section 的读取结果及共享总预算状态。"""

    content_id: str  # 实际读取的工具内容 id。
    items: tuple[ToolContentSectionReadItem, ...] = ()  # 保持请求去重后的顺序。
    budget_exhausted: bool = False  # 后续 section 可能未读取，或当前 section 仅部分返回。
