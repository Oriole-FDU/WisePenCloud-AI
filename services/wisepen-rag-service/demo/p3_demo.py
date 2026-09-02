"""使用内存 active 快照独立演示 RAG V3 P3 的四个读取/导航用例。"""

import asyncio
from dataclasses import fields, is_dataclass, replace
from pathlib import Path

from common.utils.document import DocumentChunker

from rag.application.document.models import (
    ContentRevision,
    Document,
    DocumentStructure,
)
from rag.application.outline import OutlineBuilder
from rag.application.reading import DocumentReader, SectionReadMode
from rag.domain.acl import PermissionScope


class DemoSnapshot:
    """只模拟已通过 active+ACL 前置的快照，避免 demo 依赖外部数据库。"""

    def __init__(self, document: Document) -> None:
        self.document = document

    async def load_documents(self, resource_ids, *, scope):
        return {self.document.resource_id: self.document} if self.document.resource_id in resource_ids else {}

    async def locate_sections(self, section_ids, *, scope):
        locations = {}
        for section in self.document.structure.sections:
            if section.section_id in section_ids:
                locations[section.section_id] = type("Location", (), {"document": self.document, "section": section})()
        return locations


def build_document() -> Document:
    markdown = Path(__file__).with_name("test.md").read_text(encoding="utf-8")
    chunking = DocumentChunker().chunk(markdown)
    pages = list(chunking.pages)
    ids = {
        section.section_id: f"rsec-demo-{index}"
        for index, section in enumerate(chunking.sections)
    }
    sections = [
        replace(
            section,
            section_id=ids[section.section_id],
            parent_section_id=ids.get(section.parent_section_id),
        )
        for section in chunking.sections
    ]
    return Document(
        resource_id="demo-p3",
        revision=ContentRevision.create(resource_id="demo-p3", document_version=1, raw_content=markdown),
        raw_content=markdown,
        structure=DocumentStructure(
            total_length=len(markdown),
            sections=sections,
            pages=pages,
        ),
    )


async def main() -> None:
    document = build_document()
    selected = next(
        section for section in document.structure.sections if section.level == 2
    )
    snapshot = DemoSnapshot(document)
    reader = DocumentReader(snapshots=snapshot)
    outline = OutlineBuilder(snapshots=snapshot)
    scope = PermissionScope(user_id="demo-user")
    pages = []
    if document.structure.pages:
        pages = await reader.read_pages(
            "demo-p3", [document.structure.pages[0].page_label], scope=scope
        )
    section_direct = await reader.read_sections([selected.section_id], scope=scope)
    section_recursive = await reader.read_sections([selected.section_id], mode=SectionReadMode.RECURSIVE, max_depth=1, scope=scope)
    neighborhood = await outline.neighborhood([selected.section_id], sibling_steps=1, scope=scope)
    global_outline = await outline.global_outline("demo-p3", max_level=0, scope=scope)
    review_pages = _review_list(pages)
    review_sections = _review_list(section_direct)
    review_recursive = _review_list(section_recursive)
    review_neighborhood = _review_list(neighborhood)
    output = "\n\n".join(
        [
            "# 审阅视图：DocumentReader.read_pages（换行已实际渲染）",
            review_pages,
            "# 模型真实可见视图：DocumentReader.read_pages（\\n 保持转义）",
            repr(pages),
            "# 审阅视图：DocumentReader.read_sections (DIRECT)",
            review_sections,
            "# 模型真实可见视图：DocumentReader.read_sections (DIRECT)",
            repr(section_direct),
            "# 审阅视图：DocumentReader.read_sections (RECURSIVE)",
            review_recursive,
            "# 模型真实可见视图：DocumentReader.read_sections (RECURSIVE)",
            repr(section_recursive),
            "# 审阅视图：OutlineBuilder.neighborhood",
            review_neighborhood,
            "# 模型真实可见视图：OutlineBuilder.neighborhood",
            repr(neighborhood),
            "# 审阅视图：OutlineBuilder.global_outline (max_level=0，全部层级)",
            global_outline,
            "# 模型真实可见视图：OutlineBuilder.global_outline",
            repr(global_outline),
        ]
    )
    output_path = Path(__file__).with_name("p3_demo_output.txt")
    output_path.write_text(output, encoding="utf-8")
    print(output_path)


def _review_list(values: list[object]) -> str:
    if not values:
        return "[]"
    return "\n\n".join(
        f"[{index}]\n{_review_value(value)}"
        for index, value in enumerate(values, start=1)
    )


def _review_value(value: object) -> str:
    if not is_dataclass(value):
        return str(value)
    lines: list[str] = []
    for field in fields(value):
        field_value = getattr(value, field.name)
        if field.name in {"content", "outline"} and isinstance(field_value, str):
            lines.append(f"{field.name}:\n{field_value}")
        else:
            lines.append(f"{field.name}: {field_value}")
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())
