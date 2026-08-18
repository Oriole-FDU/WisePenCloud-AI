"""当前已发布资源的统一读取契约。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from common.utils.document import OutlineNode, Section, SourceSpan

from rag.domain.models.content import ReadingBlock
from rag.domain.models.graph import GraphEvidence
from rag.domain.models.provenance import SourceEvidence
from rag.domain.models.structure import DocumentStructure


@dataclass(slots=True)
class PublishedDocumentOutline:
    """从已发布 revision 直接读取的精简目录。"""

    resource_id: str
    content_revision: str
    document_version: int
    total_length: int
    outline: list[OutlineNode] = field(default_factory=list)


@dataclass(slots=True)
class PublishedSectionContent:
    """一个 Section 的直属正文和标题树导航事实。"""

    section: Section
    text: str = ""
    parent: Section | None = None
    previous: Section | None = None
    next: Section | None = None
    children: list[Section] = field(default_factory=list)


@dataclass(slots=True)
class GraphBuildSource:
    """图抽取用例从同一已发布 revision 读取的完整输入。"""

    resource_id: str
    content_revision: str
    markdown: str
    structure: DocumentStructure
    reading_blocks: list[ReadingBlock] = field(default_factory=list)


@dataclass(slots=True)
class PublishedGraphEvidence:
    """已从当前发布 revision 核验并定位到 ReadingBlock 的图谱证据。"""

    evidence: GraphEvidence
    reading_block: ReadingBlock
    section: Section
    # Python 字符半开区间，坐标系属于 reading_block.raw_text。
    block_range: SourceSpan


class PublishedResourceRevisionError(RuntimeError):
    """请求 revision 已不再是资源当前发布版本。"""


class PublishedResourceCorruptError(RuntimeError):
    """已发布资源的正文、引用或结构归属不一致。"""


class PublishedResourceReader(Protocol):
    """从同一发布资源聚合读取 revision、结构、正文和来源证据。"""

    async def get_content_revision(self, resource_id: str) -> str | None: ...

    async def get_document_outline(
        self,
        resource_id: str,
    ) -> PublishedDocumentOutline | None: ...

    async def get_pages(
        self,
        resource_id: str,
        page_labels: Sequence[str],
    ) -> dict[str, str] | None: ...

    async def get_sections(
        self,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> dict[str, PublishedSectionContent] | None: ...

    async def get_source_evidence(
        self,
        resource_id: str,
        content_revision: str,
        source_ref_ids: Sequence[str],
    ) -> dict[str, SourceEvidence] | None: ...

    async def get_graph_evidence(
        self,
        resource_id: str,
        content_revision: str,
        evidence: Sequence[GraphEvidence],
    ) -> dict[str, PublishedGraphEvidence] | None: ...

    async def get_graph_build_source(
        self,
        resource_id: str,
        content_revision: str,
    ) -> GraphBuildSource: ...
