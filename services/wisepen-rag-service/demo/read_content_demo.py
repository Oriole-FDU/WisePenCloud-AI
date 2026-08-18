"""生成 sectioned/flat text 的确定性 Page/Section READ 评审样例。"""

import asyncio
import json
from pathlib import Path

from _demo_documents import (
    DemoDocument,
    build_demo_document,
    flat_text_markdown,
    sectioned_markdown,
)
from pydantic import TypeAdapter

from rag.application.rag.read.content import (
    DocumentContentReader,
    SectionContentView,
)
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
        pages_by_label = {
            page.page_label: page for page in document.structure.pages
        }
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
            )
        return result


async def main() -> None:
    sectioned = build_demo_document(
        resource_id="demo-rain-garden",
        markdown=sectioned_markdown(),
    )
    flat_text = build_demo_document(
        resource_id="demo-orchard-frost-log",
        markdown=flat_text_markdown(),
    )
    reader = DocumentContentReader(
        reader=_DemoPublishedResourceReader([sectioned, flat_text]),
        authorizer=_AllowAuthorizer(),
    )
    scope = PermissionScope(user_id="demo-reviewer")

    sectioned_output = await _read_document(
        reader=reader,
        document=sectioned,
        selected=next(
            section
            for section in sectioned.sections
            if section.title == "二、图谱导航"
        ),
        scope=scope,
    )
    flat_output = await _read_document(
        reader=reader,
        document=flat_text,
        selected=flat_text.sections[0],
        scope=scope,
    )

    output = "\n".join(
        [
            "=== Review notes ===",
            "- Page READ 返回整页正文和 section_id/title/section_path，不重复 preview。",
            "- Section READ 返回直属正文；flat text 通过 synthetic Section 保留 title/path/navigation。",
            "- 所有模型可见页归属统一为 page_range；纯文本没有页标记时不伪造页范围。",
            "",
            *_read_output("SECTIONED", sectioned, sectioned_output),
            "",
            *_read_output("FLAT_TEXT", flat_text, flat_output),
        ]
    )
    output_path = Path(__file__).with_name("read_content_demo_output.txt")
    output_path.write_text(output, encoding="utf-8")
    print(output_path)


async def _read_document(*, reader, document, selected, scope) -> dict[str, object]:
    pages = await reader.get_pages(
        resource_id=document.resource_id,
        page_labels=[page.page_label for page in document.structure.pages],
        permission_scope=scope,
    )
    sections = await reader.get_sections(
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


def _read_output(
    label: str,
    document: DemoDocument,
    result: dict[str, object],
) -> list[str]:
    return [
        f"=== {label} source text ===",
        document.markdown,
        f"=== {label} getPageContent ===",
        json.dumps(result["page"], ensure_ascii=False, indent=2),
        f"=== {label} getSectionContent ({result['section_id']}) ===",
        json.dumps(result["section"], ensure_ascii=False, indent=2),
    ]


if __name__ == "__main__":
    asyncio.run(main())
