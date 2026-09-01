"""P0 内容可见性底座消费的领域事实。"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from common.utils.document import Anchor, Page, Section


@dataclass(frozen=True, slots=True)
class ContentRevision:
    """一份 Markdown 内容及其上游版本的不可变身份。"""

    resource_id: str
    document_version: int
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.resource_id:
            raise ValueError("resource_id must not be empty")
        if self.document_version < 1:
            raise ValueError("document_version must be positive")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a SHA-256 hex digest")

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


def rag_section_id(
    *,
    resource_id: str,
    content_revision: str,
    common_section_id: str,
) -> str:
    """为 Common 局部 Section ID 增加资源和内容版本命名空间。"""
    identity = f"{resource_id}\0{content_revision}\0{common_section_id}"
    return f"rsec_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class DocumentStructure:
    """RAG 保留的文档结构事实；Section ID 已是全局、版本化 ID。"""

    total_length: int
    sections: tuple[Section, ...] = ()
    pages: tuple[Page, ...] = ()
    anchors: tuple[Anchor, ...] = ()

    def __post_init__(self) -> None:
        if self.total_length < 0:
            raise ValueError("total_length must not be negative")
        for section in self.sections:
            self._require_span(section.own_span.start_offset, section.own_span.end_offset)
            self._require_span(
                section.subtree_span.start_offset,
                section.subtree_span.end_offset,
            )
            for span in section.content_spans:
                self._require_span(span.start_offset, span.end_offset)
        for page in self.pages:
            self._require_span(
                page.source_span.start_offset,
                page.source_span.end_offset,
            )
        for anchor in self.anchors:
            self._require_span(
                anchor.source_span.start_offset,
                anchor.source_span.end_offset,
            )

    def _require_span(self, start_offset: int, end_offset: int) -> None:
        if start_offset < 0 or end_offset < start_offset or end_offset > self.total_length:
            raise ValueError("structure span is outside raw_content")


@dataclass(frozen=True, slots=True)
class GeneralDocumentMetadata:
    """P0 唯一支持的通用文档 metadata，垂类联合类型留到插件阶段。"""

    document_type: str = "general"


@dataclass(frozen=True, slots=True)
class Document:
    """RAG 对一份内容 revision 的权威 Markdown 和结构聚合根。"""

    resource_id: str
    revision: ContentRevision
    raw_content: str
    structure: DocumentStructure
    metadata: GeneralDocumentMetadata = field(default_factory=GeneralDocumentMetadata)

    def __post_init__(self) -> None:
        if self.revision.resource_id != self.resource_id:
            raise ValueError("document resource_id must match revision")
        if self.structure.total_length != len(self.raw_content):
            raise ValueError("structure total_length must match raw_content")
        if self.revision.content_sha256 != sha256(
            self.raw_content.encode("utf-8")
        ).hexdigest():
            raise ValueError("revision hash must match raw_content")


@dataclass(frozen=True, slots=True)
class ResourceIndexState:
    """资源 revision 的暂存与在线可见性指针。"""

    resource_id: str
    staged_content_revision: str | None = None
    staged_document_version: int | None = None
    applied_content_revision: str | None = None
