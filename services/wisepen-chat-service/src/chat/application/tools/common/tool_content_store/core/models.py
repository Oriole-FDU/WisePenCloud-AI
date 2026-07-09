from __future__ import annotations

from dataclasses import dataclass, field

Metadata = dict[str, object]  # 任意 JSON 兼容的元数据字典


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """ToolContent 中持久化的 chunk 元数据。"""

    chunk_index: int  # 当前 content 内的连续序号，从 0 开始
    start_offset: int | None = None  # 在 StoredToolContent.text 中的起始字符偏移
    end_offset: int | None = None  # 在 StoredToolContent.text 中的结束字符偏移
    block_kinds: tuple[
        str, ...
    ] = ()  # 该 chunk 覆盖的结构块类型，如 paragraph/code/table
    section_path: tuple[str, ...] = ()  # 所在章节路径，如 ("一级标题", "二级标题")
    page_label: str | None = None  # 所在页码标签，如 "3"
    anchor_labels: tuple[str, ...] = ()  # 表格、图片、公式等可定位锚点标签


@dataclass(frozen=True, slots=True)
class ToolContentIndexEntry:
    """ToolContent 读取索引项。"""

    locator_name: str  # 完整定位名，如 section:快速开始 > 安装 / page:3
    locator_kind: str  # 定位类型，如 section / page / anchor
    chunk_indices: tuple[int, ...]  # 命中的 chunk 序号集合
    start_offset: int | None = None  # 定位覆盖的原文起始 offset
    end_offset: int | None = None  # 定位覆盖的原文结束 offset
    section_path: tuple[str, ...] = ()  # section 定位对应的章节路径
    page_label: str | None = None  # page 定位对应的页码标签
    anchor_label: str | None = None  # anchor 定位对应的锚点标签


@dataclass(frozen=True, slots=True)
class ToolContentIndex:
    """ToolContent 的读取索引集合。"""

    entries: tuple[ToolContentIndexEntry, ...] = ()  # 当前 content 的所有读取索引项


@dataclass(frozen=True, slots=True)
class StoredToolContent:
    """Redis 中保存的工具内容实体。"""

    content_id: str  # ToolContentStore 生成的 cnt_* 标识
    session_id: str  # 会话隔离键，读取时必须校验
    content_type: str  # 正文 MIME 类型，如 text/markdown
    text: str  # 原始完整正文，chunk 只保存 offset 不复制正文
    chunks: tuple[ToolContentChunk, ...] = ()  # chunk 元数据集合
    index: ToolContentIndex | None = None  # 读取 selector 使用的索引集合
    metadata: Metadata = field(default_factory=dict)  # content 级附加元数据


@dataclass(frozen=True, slots=True)
class ToolContentReceipt:
    """工具内容入库后返回给调用方的存储凭证。"""

    content_id: str  # 后续读取使用的 content_id
    chunk_count: int  # 可用于 selector.chunk_indices 的 chunk 数量
    supported_selectors: tuple[str, ...] = ()  # 后续 session 读取工具可支持的 selector 类型
