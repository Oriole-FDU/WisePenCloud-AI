from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

Metadata = dict[str, object]  # 任意 JSON 兼容的元数据字典


class ToolContentRole(StrEnum):
    """ToolContent 内容角色。"""

    TOOL_OUTPUT = "tool_output"  # 原始工具输出


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """ToolContent 中持久化的 chunk 元数据。"""

    chunk_index: int  # 当前 content 内的连续序号，从 0 开始
    start_offset: int | None = None  # 在 StoredToolContent.text 中的起始字符偏移
    end_offset: int | None = None  # 在 StoredToolContent.text 中的结束字符偏移
    unit_types: tuple[str, ...] = ()  # 该 chunk 覆盖的 unit 类型，如 paragraph/code/table
    section_path: tuple[str, ...] = ()  # 所在章节路径，如 ("一级标题", "二级标题")
    anchor_names: tuple[str, ...] = ()  # 表格、图片、公式等可定位锚点名称


@dataclass(frozen=True, slots=True)
class ToolContentIndexEntry:
    """ToolContent 读取索引项。"""

    name: str  # 索引名称，如章节名、页名、锚点名
    chunk_indices: tuple[int, ...]  # 命中的 chunk 序号集合


@dataclass(frozen=True, slots=True)
class ToolContentIndex:
    """ToolContent 的读取索引集合。"""

    entries: tuple[ToolContentIndexEntry, ...] = ()  # 当前 content 的所有读取索引项


@dataclass(frozen=True, slots=True)
class StoredToolContent:
    """Redis 中保存的工具内容实体。"""

    content_id: str  # ToolContentStore 生成的 cnt_* 标识
    session_id: str  # 会话隔离键，读取时必须校验
    producer: str  # 产出方，如 tool 名称或内部组件名称
    source: str  # 内容来源，如 url、file id、skill id 或业务来源标识
    content_type: str  # 正文 MIME 类型，如 text/markdown
    content_role: str  # 内容角色，对应 ToolContentRole.value
    text: str  # 原始完整正文，chunk 只保存 offset 不复制正文
    chunks: tuple[ToolContentChunk, ...] = ()  # chunk 元数据集合
    index: ToolContentIndex | None = None  # 读取 selector 使用的索引集合
    metadata: Metadata = field(default_factory=dict)  # content 级附加元数据


@dataclass(frozen=True, slots=True)
class ToolContentReceipt:
    """工具内容入库后返回给调用方的存储凭证。"""

    content_id: str  # 后续读取使用的 content_id
    content_type: str  # 与 StoredToolContent.content_type 保持一致
    content_role: str  # 与 StoredToolContent.content_role 保持一致
    original_length: int  # 入库正文长度
    chunk_count: int  # 已生成的 chunk 数量
    read_modes: tuple[str, ...] = ()  # 后续 services 可支持的读取模式
    selectors: tuple[str, ...] = ()  # 后续 services 可支持的 selector 类型
