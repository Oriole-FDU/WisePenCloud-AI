from __future__ import annotations

from dataclasses import dataclass, replace

from common.utils.chunkers import SourceSpan


# SectionNode：标题树节点。对应“文档里的一个章节/小节”，有 section_path、父子关系、own_start/own_end/subtree_end。
# SectionReadingBlock：Section 内的阅读块。解决“一个 Section 太长，不能一次全塞给模型”的问题；
# RetrievalChunk：检索块。用于 embedding / BM25 / rerank，通常比 ReadingBlock 更小；命中后会提升回 ReadingBlock/Section。
# SourceRef：证据指针。它不是正文块，而是“这个 RetrievalChunk 对应 Kafka Markdown 哪些 source spans”的稳定引用

# Kafka Markdown
#  -> parser TextBlock  临时解析块
#  -> SectionNode       标题结构
#  -> ReadingBlock      模型阅读窗口
#  -> RetrievalChunk    检索/向量粒度
#  -> SourceRef         精确回源指针


@dataclass(frozen=True, slots=True)
class RagDocumentContent:
    """Kafka 投递的原始文档事件载荷。"""

    resource_id: str  # 私有资源 ID。
    document_version: int  # 文档版本号，用于不可变存储。
    markdown: str  # 文档归一化后的 Markdown 全文。


@dataclass(frozen=True, slots=True)
class RagSectionNode:
    """文档中的 section 节点及文档级定位信息。"""

    section_id: str  # section 全局 ID。
    resource_id: str  # section 所属资源 ID。
    document_version: int  # 资源文档版本号。
    title: str  # section 标题。
    level: int  # 标题层级，1 表示顶级。
    parent_section_id: str | None  # 父 section ID；顶级为 None。
    ordinal: int  # 同级 section 中的顺序，从 0 开始。
    section_path: tuple[str, ...]  # 从根到当前 section 的标题链路。
    preview: str  # 用于标题树导航的原文正文预览。
    own_start: int  # 当前 section 自身内容在 Markdown 中的起始 offset。
    own_end: int  # 当前 section 自身内容在 Markdown 中的结束 offset。
    subtree_end: int  # 含所有子 section 在内的结束 offset。

    def to_tree_payload(self) -> dict[str, object]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "section_path": list(self.section_path),
            "preview": self.preview,
            "has_content": self.own_end > self.own_start,
        }


@dataclass(frozen=True, slots=True)
class RagSectionReadingBlock:
    """单个 Section 内可独立读取的有界正文块。"""

    block_id: str  # 阅读块全局 ID。
    section_id: str  # 所属 Section ID。
    ordinal: int  # 在 Section 内的顺序，从 0 开始。
    raw_text: str  # 可直接返回给模型的原文。
    source_spans: tuple[SourceSpan, ...]  # Markdown 原始坐标系中的正文区间。
    page_labels: tuple[str, ...]  # 覆盖的原文页码。
    anchor_labels: tuple[str, ...]  # 包含的文档锚点。


@dataclass(frozen=True, slots=True)
class RagRetrievalChunk:
    """用于 embedding、BM25 和精排的检索子块。"""

    chunk_id: str  # chunk 全局 ID。
    chunk_index: int  # chunk 在文档中的顺序。
    reading_block_id: str  # 命中后回读的 Section ReadingBlock ID。
    section_id: str  # 唯一所属 Section ID。
    section_path: tuple[str, ...]  # 所属 Section 的标题路径。
    raw_text: str  # chunk 原文，作为证据基础。
    index_text: str  # 用于 embedding 的索引文本，可被 Contextual Indexing 注入额外上下文前缀。
    source_spans: tuple[SourceSpan, ...]  # 该 chunk 覆盖的 Markdown 区间（原始坐标系）。
    page_labels: tuple[str, ...]  # chunk 覆盖到的原文页码标签。
    anchor_labels: tuple[str, ...]  # chunk 内含的文档锚点标签。

    def with_indexing_context(self, indexing_context: str) -> RagRetrievalChunk:
        return replace(
            self,
            index_text=f"Context: {indexing_context}\n\n{self.index_text}",
        )


@dataclass(frozen=True, slots=True)
class RagSourceRef:
    """从 chunk 到原文 Authoritative Source 的稳定引用。"""

    ref_id: str  # SourceRef 稳定 ID。
    resource_id: str  # 所属资源 ID。
    document_version: int  # 资源文档版本号。
    chunk_id: str  # 对应 chunk 的 ID。
    section_id: str  # 主 source 所在 section ID。
    section_path: tuple[str, ...]  # 主 source 所在 section 路径。
    source_spans: tuple[SourceSpan, ...]  # 该 SourceRef 覆盖的 Markdown 区间集合。
    page_labels: tuple[str, ...] = ()  # 该 SourceRef 覆盖到的原文页码标签。
    anchor_labels: tuple[str, ...] = ()  # 主 source 涉及到的文档锚点。


@dataclass(frozen=True, slots=True)
class RagContentProjection:
    """资源级别的稳定内容投影，作为多后端 RAG 的统一基础。"""

    resource_id: str  # 投影所属资源。
    document_version: int  # 资源文档版本号。
    content_hash: str  # 全文内容哈希，用于变更检测。
    markdown: str  # 归一化后的 Markdown 全文（坐标基准）。
    reading_blocks: tuple[RagSectionReadingBlock, ...]  # Section 内有界阅读块。
    retrieval_chunks: tuple[RagRetrievalChunk, ...]  # 用于召回和排序的子块。
    sections: tuple[RagSectionNode, ...]  # 全部 section 树节点（扁平）。
    source_refs: tuple[RagSourceRef, ...]  # 全部 SourceRef 列表，作为证据回源入口。
