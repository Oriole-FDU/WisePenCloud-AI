"""从 Mongo 读取当前已发布资源的统一 adapter。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256

from common.utils.document import Anchor, OutlineNode, Page, Section, SourceSpan

from rag.domain.entities import (
    ContentRevisionEntity,
    ReadingBlockEntity,
    ResourceIndexStateEntity,
    SectionEntity,
    SourcePartEntity,
    SourceRefEntity,
)
from rag.domain.models.content import (
    ReadingBlock,
)
from rag.domain.models.graph import GraphEvidence
from rag.domain.models.provenance import SourceEvidence, SourceRef
from rag.domain.models.structure import DocumentStructure, StructureMode
from rag.domain.repositories.mongo.published_resource_reader import (
    GraphBuildSource,
    PublishedDocumentOutline,
    PublishedGraphEvidence,
    PublishedResourceCorruptError,
    PublishedResourceReader,
    PublishedResourceRevisionError,
    PublishedSectionContent,
)

from ._source_parts import SourcePart, assemble_source_text


@dataclass(slots=True)
class _PublishedRevision:
    """Mongo revision 文档与独立 Section collection 的内部连接点。"""

    resource_id: str
    content_revision: str
    document_version: int
    content_hash: str
    structure_mode: StructureMode
    total_length: int
    outline: list[OutlineNode] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)


class MongoPublishedResourceReader(PublishedResourceReader):
    """统一读取一个资源当前发布 revision 的结构、正文和来源证据。"""

    async def get_content_revision(self, resource_id: str) -> str | None:
        revision = await self._get_published_revision(resource_id)
        return revision.content_revision if revision is not None else None

    @staticmethod
    async def _get_published_revision(resource_id: str,) -> _PublishedRevision | None:
        state = await ResourceIndexStateEntity.find_one({"resource_id": resource_id})
        if state is None or state.applied_content_revision is None:
            return None

        entity = await ContentRevisionEntity.find_one(
            {
                "resource_id": resource_id,
                "content_revision": state.applied_content_revision,
            }
        )
        if entity is None:
            raise PublishedResourceCorruptError(
                f"resource {resource_id} published revision is missing"
            )
        return _to_published_revision(entity)

    async def get_document_outline(
        self,
        resource_id: str,
    ) -> PublishedDocumentOutline | None:
        revision = await self._get_published_revision(resource_id)
        if revision is None:
            return None

        return PublishedDocumentOutline(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
            document_version=revision.document_version,
            total_length=revision.total_length,
            outline=revision.outline,
        )

    async def get_pages(
        self,
        resource_id: str,
        page_labels: Sequence[str],
    ) -> dict[str, str] | None:
        revision = await self._get_published_revision(resource_id)
        if revision is None:
            return None

        pages_by_label = {page.page_label: page for page in revision.pages}
        selected = [
            pages_by_label[label]
            for label in dict.fromkeys(page_labels)
            if label in pages_by_label
        ]
        result: dict[str, str] = {}
        for page in selected:
            parts = await self._get_parts(
                revision.content_revision,
                [page.source_span],
            )
            result[page.page_label] = assemble_source_text(parts, [page.source_span])
        return result

    async def get_sections(
        self,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> dict[str, PublishedSectionContent] | None:
        revision = await self._get_published_revision(resource_id)
        if revision is None:
            return None

        requested_ids = list(dict.fromkeys(section_ids))
        if not requested_ids:
            return {}

        all_sections = await self._get_sections_for_revision(
            resource_id,
            revision.content_revision,
        )
        sections_by_id = {section.section_id: section for section in all_sections}
        selected = [
            sections_by_id[section_id]
            for section_id in requested_ids
            if section_id in sections_by_id
        ]
        if not selected:
            return {}

        requested_spans = [
            span for section in selected for span in section.content_spans
        ]
        parts = (
            await self._get_parts(revision.content_revision, requested_spans)
            if requested_spans
            else []
        )
        siblings_by_parent: dict[str | None, list[Section]] = {}
        for section in all_sections:
            siblings_by_parent.setdefault(section.parent_section_id, []).append(section)
        for siblings in siblings_by_parent.values():
            siblings.sort(key=lambda section: section.ordinal)

        result: dict[str, PublishedSectionContent] = {}
        for section in selected:
            siblings = siblings_by_parent[section.parent_section_id]
            index = next(
                index
                for index, sibling in enumerate(siblings)
                if sibling.section_id == section.section_id
            )
            result[section.section_id] = PublishedSectionContent(
                section=section,
                text=assemble_source_text(parts, section.content_spans),
                parent=sections_by_id.get(section.parent_section_id),
                previous=siblings[index - 1] if index else None,
                next=siblings[index + 1] if index + 1 < len(siblings) else None,
                children=siblings_by_parent.get(section.section_id, []),
            )
        return result

    async def get_source_evidence(
        self,
        resource_id: str,
        content_revision: str,
        source_ref_ids: Sequence[str],
    ) -> dict[str, SourceEvidence] | None:
        revision = await self._get_published_revision(resource_id)
        if revision is None:
            return None
        self._require_revision(revision, content_revision)

        full_span = SourceSpan(0, revision.total_length)
        parts = await self._get_parts(content_revision, [full_span])
        full_text = assemble_source_text(parts, [full_span])
        if sha256(full_text.encode("utf-8")).hexdigest() != revision.content_hash:
            raise PublishedResourceCorruptError(
                f"content revision {content_revision} hash does not match source parts"
            )

        requested_ids = list(dict.fromkeys(source_ref_ids))
        if not requested_ids:
            return {}
        ref_entities = await SourceRefEntity.find(
            {
                "resource_id": resource_id,
                "content_revision": content_revision,
                "ref_id": {"$in": requested_ids},
            }
        ).to_list()
        refs = [_to_source_ref(entity) for entity in ref_entities]

        blocks = await ReadingBlockEntity.find(
            {
                "resource_id": resource_id,
                "content_revision": content_revision,
                "block_id": {"$in": list({ref.reading_block_id for ref in refs})},
            }
        ).to_list()
        blocks_by_id = {entity.block_id: _to_reading_block(entity) for entity in blocks}
        sections = await SectionEntity.find(
            {
                "resource_id": resource_id,
                "content_revision": content_revision,
                "section_id": {
                    "$in": list(
                        {
                            section_id
                            for block in blocks_by_id.values()
                            for section_id in block.section_ids
                        }
                    )
                },
            }
        ).to_list()
        sections_by_id = {entity.section_id: _to_section(entity) for entity in sections}

        evidence: dict[str, SourceEvidence] = {}
        for source_ref in refs:
            block = blocks_by_id.get(source_ref.reading_block_id)
            section = sections_by_id.get(source_ref.section_id)
            if block is None or section is None:
                raise PublishedResourceCorruptError(
                    f"source ref {source_ref.ref_id} has missing ownership records"
                )
            block_sections = _reading_block_sections(block, sections_by_id)
            if source_ref.section_id not in block.section_ids:
                raise PublishedResourceCorruptError(
                    f"source ref {source_ref.ref_id} has an invalid block owner"
                )
            evidence[source_ref.ref_id] = SourceEvidence(
                source_ref=source_ref,
                reading_block=block,
                section=section,
                source_text=assemble_source_text(parts, source_ref.source_spans),
                reading_block_sections=block_sections,
            )
        return evidence

    async def get_graph_build_source(
        self,
        resource_id: str,
        content_revision: str,
    ) -> GraphBuildSource:
        revision = await self._get_published_revision(resource_id)
        if revision is None:
            raise PublishedResourceRevisionError(
                f"resource {resource_id} has no published revision"
            )
        self._require_revision(revision, content_revision)

        full_span = SourceSpan(0, revision.total_length)
        parts = await self._get_parts(content_revision, [full_span])
        sections = await SectionEntity.find(
            {"resource_id": resource_id, "content_revision": content_revision}
        ).to_list()
        blocks = (
            await ReadingBlockEntity.find(
                {"resource_id": resource_id, "content_revision": content_revision}
            )
            .sort([("start_offset", 1), ("ordinal", 1)])
            .to_list()
        )
        return GraphBuildSource(
            resource_id=resource_id,
            content_revision=content_revision,
            markdown=assemble_source_text(parts, [full_span]),
            structure=DocumentStructure(
                mode=revision.structure_mode,
                total_length=revision.total_length,
                sections=[_to_section(entity) for entity in sections],
                pages=revision.pages,
                anchors=revision.anchors,
            ),
            reading_blocks=[_to_reading_block(entity) for entity in blocks],
        )

    async def get_graph_evidence(
        self,
        resource_id: str,
        content_revision: str,
        evidence: Sequence[GraphEvidence],
    ) -> dict[str, PublishedGraphEvidence] | None:
        """把图谱原文坐标解析为当前发布 ReadingBlock 内的精确区间。"""
        revision = await self._get_published_revision(resource_id)
        if revision is None:
            return None
        self._require_revision(revision, content_revision)

        # 调用方（GraphEvidenceVerifier）已按 resource+revision 分组，条目归属恒成立。
        requested: dict[str, GraphEvidence] = {}
        for item in evidence:
            existing = requested.get(item.evidence_id)
            if existing is not None and existing != item:
                raise PublishedResourceCorruptError(
                    f"graph evidence {item.evidence_id} has conflicting payloads"
                )
            requested[item.evidence_id] = item
        if not requested:
            return {}

        full_span = SourceSpan(0, revision.total_length)
        parts = await self._get_parts(content_revision, [full_span])
        markdown = assemble_source_text(parts, [full_span])
        if sha256(markdown.encode("utf-8")).hexdigest() != revision.content_hash:
            raise PublishedResourceCorruptError(
                f"content revision {content_revision} hash does not match source parts"
            )

        block_entities = await ReadingBlockEntity.find(
            {
                "resource_id": resource_id,
                "content_revision": content_revision,
                "block_id": {
                    "$in": list(
                        {item.reading_block_id for item in requested.values()}
                    )
                },
            }
        ).to_list()
        blocks_by_id = {
            entity.block_id: _to_reading_block(entity) for entity in block_entities
        }
        section_entities = await SectionEntity.find(
            {
                "resource_id": resource_id,
                "content_revision": content_revision,
                "section_id": {
                    "$in": list(
                        {
                            section_id
                            for block in blocks_by_id.values()
                            for section_id in block.section_ids
                        }
                    )
                },
            }
        ).to_list()
        sections_by_id = {
            entity.section_id: _to_section(entity) for entity in section_entities
        }

        resolved: dict[str, PublishedGraphEvidence] = {}
        for item in requested.values():
            block = blocks_by_id.get(item.reading_block_id)
            if block is None:
                raise PublishedResourceCorruptError(
                    f"graph evidence {item.evidence_id} has no ReadingBlock"
                )
            if (
                item.source_span.start_offset < 0
                or item.source_span.end_offset > len(markdown)
                or markdown[
                    item.source_span.start_offset : item.source_span.end_offset
                ]
                != item.quote
            ):
                raise PublishedResourceCorruptError(
                    f"graph evidence {item.evidence_id} does not match markdown"
                )

            block_range = _relative_graph_range(block, item)
            if block.raw_text[
                block_range.start_offset : block_range.end_offset
            ] != item.quote:
                raise PublishedResourceCorruptError(
                    f"graph evidence {item.evidence_id} does not match ReadingBlock"
                )
            section = _section_for_span(block, item.source_span, sections_by_id)
            if section is None:
                raise PublishedResourceCorruptError(
                    f"graph evidence {item.evidence_id} has no owning Section"
                )
            resolved[item.evidence_id] = PublishedGraphEvidence(
                evidence=item,
                reading_block=block,
                section=section,
                block_range=block_range,
                reading_block_sections=_reading_block_sections(block, sections_by_id),
            )
        return resolved

    @staticmethod
    async def _get_sections_for_revision(
        resource_id: str,
        content_revision: str,
    ) -> list[Section]:
        entities = (
            await SectionEntity.find(
                {
                    "resource_id": resource_id,
                    "content_revision": content_revision,
                }
            )
            .sort("+own_start")
            .to_list()
        )
        return [_to_section(entity) for entity in entities]

    @staticmethod
    async def _get_parts(
        content_revision: str,
        source_spans: Sequence[SourceSpan] | None = None,
    ) -> list[SourcePart]:
        query: dict[str, object] = {"content_revision": content_revision}
        if source_spans:
            query.update(
                {
                    "start_offset": {
                        "$lt": max(span.end_offset for span in source_spans)
                    },
                    "end_offset": {
                        "$gt": min(span.start_offset for span in source_spans)
                    },
                }
            )
        entities = await SourcePartEntity.find(query).sort("+part_index").to_list()
        return [_to_source_part(entity) for entity in entities]

    @staticmethod
    def _require_revision(
        revision: _PublishedRevision,
        content_revision: str,
    ) -> None:
        if revision.content_revision != content_revision:
            raise PublishedResourceRevisionError(
                f"content revision {content_revision} is not published for "
                f"{revision.resource_id}"
            )


def _to_published_revision(record: ContentRevisionEntity) -> _PublishedRevision:
    return _PublishedRevision(
        resource_id=record.resource_id,
        content_revision=record.content_revision,
        document_version=record.document_version,
        content_hash=record.content_hash,
        structure_mode=StructureMode(record.structure_mode),
        total_length=record.total_length,
        outline=record.outline,
        pages=[
            Page(
                page_index=page.page_index,
                page_label=page.page_label,
                source_span=SourceSpan(page.start_offset, page.end_offset),
            )
            for page in record.pages
        ],
        anchors=[
            Anchor(
                label=anchor.label,
                source_span=SourceSpan(anchor.start_offset, anchor.end_offset),
            )
            for anchor in record.anchors
        ],
    )


def _to_source_part(record: SourcePartEntity) -> SourcePart:
    return SourcePart(
        resource_id=record.resource_id,
        content_revision=record.content_revision,
        part_index=record.part_index,
        source_span=SourceSpan(record.start_offset, record.end_offset),
        text=record.text,
    )


def _to_section(record: SectionEntity) -> Section:
    return Section(
        section_id=record.section_id,
        title=record.title,
        level=record.level,
        parent_section_id=record.parent_section_id,
        ordinal=record.ordinal,
        section_path=tuple(record.section_path),
        own_span=SourceSpan(record.own_start, record.own_end),
        subtree_span=SourceSpan(record.own_start, record.subtree_end),
        content_spans=[
            SourceSpan(span.start_offset, span.end_offset)
            for span in record.content_spans
        ],
        preview=record.preview,
    )


def _to_reading_block(record: ReadingBlockEntity) -> ReadingBlock:
    return ReadingBlock(
        block_id=record.block_id,
        section_ids=list(record.section_ids),
        ordinal=record.ordinal,
        raw_text=record.raw_text,
        source_spans=[
            SourceSpan(span.start_offset, span.end_offset)
            for span in record.source_spans
        ],
        page_labels=list(record.page_labels),
        anchor_labels=list(record.anchor_labels),
    )


def _section_for_span(
    block: ReadingBlock,
    source_span: SourceSpan,
    sections_by_id: dict[str, Section],
) -> Section | None:
    """根据精确证据区间解析多 Section 父块中的实际归属 Section。"""
    for section_id in block.section_ids:
        section = sections_by_id.get(section_id)
        if section is not None and any(
            source_span.start_offset >= content_span.start_offset
            and source_span.end_offset <= content_span.end_offset
            for content_span in section.content_spans
        ):
            return section
    return None


def _reading_block_sections(
    block: ReadingBlock,
    sections_by_id: dict[str, Section],
) -> list[Section]:
    """按父块声明顺序返回其 Section；缺失记录意味着发布数据不完整。"""
    sections = [
        sections_by_id[section_id]
        for section_id in block.section_ids
        if section_id in sections_by_id
    ]
    if len(sections) != len(block.section_ids):
        raise PublishedResourceCorruptError(
            f"ReadingBlock {block.block_id} has missing Sections"
        )
    return sections


def _to_source_ref(record: SourceRefEntity) -> SourceRef:
    return SourceRef(
        ref_id=record.ref_id,
        resource_id=record.resource_id,
        content_revision=record.content_revision,
        chunk_id=record.chunk_id,
        reading_block_id=record.reading_block_id,
        section_id=record.section_id,
        section_path=list(record.section_path),
        source_spans=[
            SourceSpan(span.start_offset, span.end_offset)
            for span in record.source_spans
        ],
        page_labels=list(record.page_labels),
        anchor_labels=list(record.anchor_labels),
    )


def _relative_graph_range(
    block: ReadingBlock,
    evidence: GraphEvidence,
) -> SourceSpan:
    """把单一原文 span 映射为 ReadingBlock 拼接文本中的字符半开区间。"""
    block_offset = 0
    for index, block_span in enumerate(block.source_spans):
        if (
            evidence.source_span.start_offset >= block_span.start_offset
            and evidence.source_span.end_offset <= block_span.end_offset
        ):
            start = (
                block_offset
                + evidence.source_span.start_offset
                - block_span.start_offset
            )
            return SourceSpan(start, start + len(evidence.quote))
        block_offset += block_span.end_offset - block_span.start_offset
        if index + 1 < len(block.source_spans):
            block_offset += 2
    raise PublishedResourceCorruptError(
        f"graph evidence {evidence.evidence_id} is outside its ReadingBlock"
    )
