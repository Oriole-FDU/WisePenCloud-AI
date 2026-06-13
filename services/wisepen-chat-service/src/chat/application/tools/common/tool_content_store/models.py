from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from chat.application.tools.common.chunking_engine import ChunkLevel, IndexKind


type Metadata = dict[str, object]


class ToolContentRole(StrEnum):
    """ToolContent 内容角色。"""

    TOOL_OUTPUT = "tool_output"  # 原始工具输出
    MODEL_CONTEXT_RENDERED = "model_context_rendered"  # 已渲染给模型看的上下文文本
    MODEL_CONTEXT_RECEIPT = "model_context_receipt"  # 指向可读取内容的上下文凭证
    MODEL_CONTEXT_PAYLOAD = "model_context_payload"  # 上下文结构化 payload
    MODEL_CONTEXT_WINDOW = "model_context_window"  # 后续读取产生的内容窗口
    PARSED = "parsed"  # 从原始工具输出解析出的派生内容


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """ToolContent 中持久化的 chunk 元数据。"""

    chunk_id: str  # chunk 全局标识
    chunk_index: int  # 当前 content 内的连续序号，从 0 开始
    level: ChunkLevel = ChunkLevel.DEFAULT  # chunk 层级，用于父子窗口或层级读取
    parent_chunk_id: str | None = None  # 父 chunk id，普通扁平分块为空
    start_offset: int | None = None  # 在 StoredToolContent.text 中的起始字符偏移
    end_offset: int | None = None  # 在 StoredToolContent.text 中的结束字符偏移
    start_unit: int | None = None  # 对应 chunking unit 的起始序号
    end_unit: int | None = None  # 对应 chunking unit 的结束序号
    content_hash: str = ""  # chunk 正文 hash，用于去重或诊断
    unit_types: tuple[str, ...] = ()  # 该 chunk 覆盖的 unit 类型，如 paragraph/code/table
    section_path: tuple[str, ...] = ()  # 所在章节路径，如 ("一级标题", "二级标题")
    anchor_names: tuple[str, ...] = ()  # 表格、图片、公式等可定位锚点名称
    page_name: str | None = None  # 页名或页码标识，适用于 PDF/分页来源
    metadata: Metadata = field(default_factory=dict)  # chunking engine 透传的附加元数据


@dataclass(frozen=True, slots=True)
class ToolContentIndexEntry:
    """ToolContent 读取索引项。"""

    name: str  # 索引名称，如章节名、页名、锚点名
    kind: IndexKind  # 索引类型，如 section/page/anchor/unit_type
    chunk_indices: tuple[int, ...]  # 命中的 chunk 序号集合
    chunk_ids: tuple[str, ...] = ()  # 命中的 chunk id 集合，便于跨序号定位
    start_offset: int | None = None  # 索引覆盖范围的起始字符偏移
    end_offset: int | None = None  # 索引覆盖范围的结束字符偏移
    metadata: Metadata = field(default_factory=dict)  # indexer 透传的附加元数据


@dataclass(frozen=True, slots=True)
class ToolContentIndex:
    """ToolContent 的读取索引集合。"""

    entries: tuple[ToolContentIndexEntry, ...] = ()  # 当前 content 的所有读取索引项

    def entries_by_kind(self, kind: IndexKind) -> tuple[ToolContentIndexEntry, ...]:
        """按索引类型取 entries。"""
        return tuple(entry for entry in self.entries if entry.kind == kind)

    def chunk_indices_by_kind(self, kind: IndexKind, name: str) -> tuple[int, ...]:
        """按索引类型和名称取 chunk indices。"""
        for entry in self.entries:
            if entry.kind == kind and entry.name == name:
                return entry.chunk_indices
        return ()


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
    producer: str  # 与 StoredToolContent.producer 保持一致
    source: str  # 与 StoredToolContent.source 保持一致
    content_type: str  # 与 StoredToolContent.content_type 保持一致
    content_role: str  # 与 StoredToolContent.content_role 保持一致
    original_length: int  # 入库正文长度
    chunk_count: int  # 已生成的 chunk 数量
    index_summary: dict[str, int] = field(default_factory=dict)  # 各索引类型数量摘要
    read_modes: tuple[str, ...] = ()  # 后续 tool_content_read 可支持的读取模式
    selectors: tuple[str, ...] = ()  # 后续 tool_content_read 可支持的 selector 类型
    cached: bool = True  # 是否已入缓存；当前 store 成功返回即为 True
    metadata: Metadata = field(default_factory=dict)  # 返回给调用方的 content 级元数据
