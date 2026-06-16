from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ToolContentReadMode(StrEnum):
    """ToolContentRead 支持的读取模式（字符串枚举）。"""

    CONTINUOUS = "continuous"      # 按字符 offset 连续读取
    RANKED_EXPAND = "ranked_expand"  # 在候选 chunk 内排序后展开窗口
    REGEX_MATCH = "regex_match"    # 在候选 chunk 内正则匹配后展开窗口


@dataclass(frozen=True, slots=True)
class ToolContentSelector:
    """读取前置候选域过滤器。

    所有条件同时存在时取交集（AND 逻辑）；为空则表示不过滤。
    - unit_types: 按块内单元类型过滤（如 code/table/formula）
    - sections/page/anchors: 按结构化索引名称过滤
    - chunk_indices: 显式指定 chunk 序号（精准定位）
    - include_unknown: 是否保留"无结构元数据"的 chunk，默认 False
    """

    unit_types: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    pages: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    chunk_indices: tuple[int, ...] = ()
    include_unknown: bool = False


@dataclass(frozen=True, slots=True)
class ToolContentReadRequest:
    """ToolContentRead 内部请求，包含所有读取参数。"""

    content_ids: tuple[str, ...]                                           # 要批量读取的内容 ID 集合
    mode: ToolContentReadMode = ToolContentReadMode.CONTINUOUS             # 读取模式
    selector: ToolContentSelector | None = None                            # 前置过滤器
    offset: int | None = None                                              # continuous 模式：字符偏移起点
    limit: int | None = None                                               # continuous 模式：最大字符数
    query: str | None = None                                               # ranked_expand 模式：排序查询文本
    top_k: int = 5                                                         # ranked_expand 模式：返回 Top-K
    pattern: str | None = None                                             # regex_match 模式：正则模式串
    max_matches: int = 10                                                  # regex_match 模式：最大匹配数
    merge_before: int = 0                                                  # 窗口向前合并的 chunk 数
    merge_after: int = 0                                                   # 窗口向后合并的 chunk 数


@dataclass(frozen=True, slots=True)
class ToolContentWindow:
    """一次读取产生的模型上下文窗口。

    包含文本内容及其在原文中的定位信息（offset、chunk 范围）。
    ranked_expand 模式额外携带 rank/score，regex_match 模式携带 match_text。
    """

    text: str                               # 窗口文本内容
    start_offset: int | None = None         # 在原文中的起始字符偏移
    end_offset: int | None = None           # 在原文中的结束字符偏移
    center_chunk: int | None = None         # 中心 chunk 序号
    chunk_start: int | None = None          # 窗口起始 chunk 序号
    chunk_end: int | None = None            # 窗口结束 chunk 序号


@dataclass(frozen=True, slots=True)
class ToolContentReadItemResult:
    """单个 ToolContent 的读取结果。"""

    content_id: str                          # 实际读取的内容 ID（可能经过重定向）
    status: str                              # success 或 failed
    windows: tuple[ToolContentWindow, ...] = ()  # 读取产生的窗口列表
    reason: str | None = None                # 单项失败原因
