"""P0 内容可见性底座消费的领域事实。"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from common.utils.document import Anchor, Page, Section, SourceSpan

from rag.application.plugins.core.metadata import (
    DocChunkMetadata,
    DocumentMetadata,
    GeneralChunkMetadata,
    GeneralDocumentMetadata,
)

# --- 资源可见性 ---

@dataclass(frozen=True, slots=True)
class ResourceIndexState:
    """资源 revision 的暂存与在线可见性指针。"""

    resource_id: str
    staged_content_revision: str | None = None
    staged_document_version: int | None = None
    applied_content_revision: str | None = None


# --- 文档身份标识 ---

def rag_section_id(
    *,
    resource_id: str,
    content_revision: str,
    common_section_id: str,
) -> str:
    """为 Common 局部 Section ID 增加资源和内容版本命名空间。"""
    identity = f"{resource_id}\0{content_revision}\0{common_section_id}"
    return f"rsec_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def rag_chunk_id(
    *,
    resource_id: str,
    content_revision: str,
    common_chunk_id: str,
) -> str:
    """为 Common 局部 Chunk ID 增加资源和内容版本命名空间。"""
    identity = f"{resource_id}\0{content_revision}\0{common_chunk_id}"
    return f"rchk_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class ContentRevision:
    """一份 Markdown 内容及其上游版本的不可变身份。"""

    resource_id: str
    document_version: int
    content_sha256: str

    @classmethod
    def create(
        cls,
        *,
        resource_id: str,
        document_version: int,
        raw_content: str,
    ) -> ContentRevision:
        return cls(
            resource_id=resource_id,
            document_version=document_version,
            content_sha256=sha256(raw_content.encode("utf-8")).hexdigest(),
        )

    @property
    def content_revision(self) -> str:
        return (
            f"{self.resource_id}@{self.document_version}"
            f"#{self.content_sha256[:16]}"
        )


# --- 文档结构与分块 ---

@dataclass(frozen=True, slots=True)
class Document:
    """RAG 对一份内容 revision 的权威 Markdown 和结构聚合根。"""

    resource_id: str
    revision: ContentRevision
    raw_content: str
    structure: DocumentStructure
    metadata: DocumentMetadata = field(default_factory=GeneralDocumentMetadata)


@dataclass(frozen=True, slots=True)
class DocumentStructure:
    """RAG 保留的文档结构事实；Section ID 已是全局、版本化 ID。"""

    total_length: int
    sections: list[Section] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DocChunk:
    """RAG 的检索原子；正文与坐标来自 Common 的一次分块结果。"""
    # 身份凭据
    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None  # 直属 Section，仅在有真实 Section 时才有值；flat 文本保持 None

    # 原文与坐标
    chunk_index: int  # 块在原文中的全局顺序索引，用于滑动窗口构建
    raw_text: str
    source_spans: list[SourceSpan]  # 原文 Python 字符半开区间，允许一个 Chunk 覆盖多个完整 block

    # 可选的语义标签
    section_path: list[str] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    anchor_labels: list[str] = field(default_factory=list)

    # 依赖 LLM 的可选语义增强
    contextual_prefix: str = ""
    key_terms: list[str] = field(default_factory=list)
    extracted_node_ids: list[str] = field(default_factory=list)

    # 依赖插件的可选 metadata，用于垂类拓展；通用 Chunk 仅支持 GeneralChunkMetadata
    metadata: DocChunkMetadata = field(default_factory=GeneralChunkMetadata)

    def __post_init__(self) -> None:
        if not self.source_spans:
            raise ValueError("DocChunk requires source_spans")
        if any(
            span.start_offset < 0 or span.start_offset >= span.end_offset
            for span in self.source_spans
        ):
            raise ValueError("DocChunk source spans must be non-empty half-open ranges")

    @property
    def chunk_span(self) -> SourceSpan:
        """返回覆盖 Chunk 所有来源片段的最小连续范围。"""
        return SourceSpan(
            min(span.start_offset for span in self.source_spans),
            max(span.end_offset for span in self.source_spans),
        )

    def is_valid_for(self, document: Document) -> bool:
        """确认 Chunk 的所有坐标都落在给定权威 Markdown 内。"""
        return all(
            span.end_offset <= len(document.raw_content)
            for span in self.source_spans
        )

    def get_full_text(self) -> str:
        """返回带标题路径的完整 Chunk 文本，供排序和阅读上下文使用。"""
        title = " > ".join(self.section_path)
        return f"{title}\n\n{self.raw_text}" if title else self.raw_text

    def get_semantic_text(self) -> str:
        """构建 Dense 输入；不将该拼接结果作为持久化字段。"""
        parts: list[str] = []
        if self.section_path:
            parts.append(" > ".join(self.section_path))
        if self.contextual_prefix.strip():
            parts.append(self.contextual_prefix.strip())
        parts.append(self.raw_text)
        return "\n\n".join(parts)

    def get_lexical_text(self) -> str:
        """构建 BM25 输入；不将该拼接结果作为持久化字段。"""
        parts: list[str] = []
        if self.section_path:
            parts.append(" ".join(self.section_path))
        if self.key_terms:
            parts.append(" ".join(self.key_terms))
        parts.append(self.raw_text)
        return " ".join(parts)
