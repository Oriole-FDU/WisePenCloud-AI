"""生成 sectioned/flat text 的确定性 Page/Section READ 评审样例。"""

import asyncio
import json
from pathlib import Path

from _demo_documents import (
    DemoDocument,
    build_demo_document,
    flat_text_markdown,
)
from pydantic import TypeAdapter

from rag.api.schemas import SurroundingOutlineResponse
from rag.application.rag.read.content import (
    DocumentContentReader,
    SectionContentView,
)
from rag.application.rag.read.neighborhood import SectionNeighborhoodReader
from rag.domain.models.acl import PermissionScope
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedSectionContent,
)


class _AllowAuthorizer:
    async def authorize_resource(self, *, resource_id, scope) -> bool:
        return True


class _DemoPublishedResourceReader:
    """模拟 Mongo SourcePart 读取，正文与结构均来自生产构造器产物。"""

    def __init__(self, documents: list[DemoDocument]) -> None:
        self._documents = {document.resource_id: document for document in documents}

    async def get_pages(self, resource_id, page_labels):
        document = self._documents[resource_id]
        pages_by_label = {page.page_label: page for page in document.structure.pages}
        result = {}
        for label in dict.fromkeys(page_labels):
            page = pages_by_label.get(label)
            if page is None:
                continue
            result[label] = document.markdown[
                page.source_span.start_offset : page.source_span.end_offset
            ]
        return result

    async def get_sections(self, resource_id, section_ids):
        document = self._documents[resource_id]
        sections_by_id = {section.section_id: section for section in document.sections}
        siblings_by_parent = {}
        for section in document.sections:
            siblings_by_parent.setdefault(section.parent_section_id, []).append(section)
        for siblings in siblings_by_parent.values():
            siblings.sort(key=lambda section: section.ordinal)

        result = {}
        for section_id in dict.fromkeys(section_ids):
            section = sections_by_id.get(section_id)
            if section is None:
                continue
            siblings = siblings_by_parent[section.parent_section_id]
            index = siblings.index(section)
            result[section_id] = PublishedSectionContent(
                section=section,
                text="\n\n".join(
                    document.markdown[span.start_offset : span.end_offset]
                    for span in section.content_spans
                ),
                parent=sections_by_id.get(section.parent_section_id),
                previous=siblings[index - 1] if index else None,
                next=(siblings[index + 1] if index + 1 < len(siblings) else None),
                children=siblings_by_parent.get(section_id, []),
                page_labels=[
                    page.page_label
                    for page in document.structure.pages
                    if _spans_overlap(page.source_span, section.subtree_span)
                ],
                anchor_labels=[
                    anchor.label
                    for anchor in document.structure.anchors
                    if _spans_overlap(anchor.source_span, section.own_span)
                ],
            )
        return result


def _spans_overlap(left, right) -> bool:
    return left.start_offset < right.end_offset and right.start_offset < left.end_offset


async def main() -> None:
    test1_path = Path(__file__).with_name("test1.md")
    sectioned = build_demo_document(
        resource_id="demo-transformer-paper",
        markdown=test1_path.read_text(encoding="utf-8"),
    )
    flat_text = build_demo_document(
        resource_id="demo-orchard-frost-log",
        markdown=flat_text_markdown(),
    )
    published_reader = _DemoPublishedResourceReader([sectioned, flat_text])
    reader = DocumentContentReader(
        reader=published_reader,
        authorizer=_AllowAuthorizer(),
    )
    neighborhood_reader = SectionNeighborhoodReader(
        reader=published_reader,
        authorizer=_AllowAuthorizer(),
    )
    scope = PermissionScope(user_id="demo-reviewer")

    sectioned_output = await _read_document(
        reader=reader,
        document=sectioned,
        selected=next(
            section
            for section in sectioned.sections
            if section.title == "3.2 Attention"
        ),
        scope=scope,
    )
    flat_output = await _read_document(
        reader=reader,
        document=flat_text,
        selected=flat_text.sections[0],
        scope=scope,
    )
    navigation_output = await _neighborhood_output(
        reader=neighborhood_reader,
        document=sectioned,
        selected=next(
            section
            for section in sectioned.sections
            if section.title == "3.2 Attention"
        ),
        scope=scope,
    )

    output = "\n".join(
        [
            "=== Review notes ===",
            "- Page READ 返回整页正文和 section_id/title/section_path，不重复 preview。",
            "- getSurroundingOutline 只返回命中 Section 的 parent、siblings、children 元信息。",
            "- readSections 只返回选定 Section 的完整直属正文。",
            "- 所有模型可见页归属统一为 page_range；纯文本没有页标记时不伪造页范围。",
            "",
            *_read_output("TEST1", sectioned, sectioned_output),
            "",
            *navigation_output,
            "",
            "",
            *_read_output("FLAT_TEXT", flat_text, flat_output),
        ]
    )
    output_path = Path(__file__).with_name("read_content_demo_output.txt")
    output_path.write_text(output, encoding="utf-8")
    print(output_path)


async def _read_document(*, reader, document, selected, scope) -> dict[str, object]:
    pages = await reader.read_pages(
        resource_id=document.resource_id,
        page_labels=[page.page_label for page in document.structure.pages],
        permission_scope=scope,
    )
    sections = await reader.read_sections(
        resource_id=document.resource_id,
        section_ids=[selected.section_id],
        permission_scope=scope,
    )
    page_payload = pages
    section_payload = TypeAdapter(dict[str, SectionContentView]).dump_python(
        sections,
        mode="json",
        exclude_none=True,
    )
    return {
        "page": page_payload,
        "section_id": selected.section_id,
        "section": section_payload[selected.section_id],
    }


async def _neighborhood_output(*, reader, document, selected, scope) -> list[str]:
    """展示命中 Section 周围的无正文局部标题树。"""
    result = await reader.get_surrounding_outline(
        resource_id=document.resource_id,
        section_id=selected.section_id,
        window_size=2,
        permission_scope=scope,
    )
    payload = SurroundingOutlineResponse.model_validate(
        result,
        from_attributes=True,
    ).model_dump(
        exclude_defaults=True,
        exclude_none=True,
        by_alias=True,
    )
    return [
        "=== TEST1 getSurroundingOutline ===",
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
    ]


def _read_output(
    label: str,
    document: DemoDocument,
    result: dict[str, object],
) -> list[str]:
    return [
        f"=== {label} source text ===",
        document.markdown,
        f"=== {label} readPages ===",
        json.dumps(result["page"], ensure_ascii=False, indent=2),
        f"=== {label} readSections ({result['section_id']}) ===",
        json.dumps(result["section"], ensure_ascii=False, indent=2),
    ]


if __name__ == "__main__":
    asyncio.run(main())
