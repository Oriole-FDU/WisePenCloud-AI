"""从 active Document 提供 Page 与 Section 的确定性读取。"""

from dataclasses import dataclass
from enum import StrEnum

from common.utils.document import Section

from rag_v3.application.snapshot import ActiveDocumentSnapshotLoader
from rag_v3.domain.acl import PermissionScope


class SectionReadMode(StrEnum):
    DIRECT = "direct"
    RECURSIVE = "recursive"


@dataclass(frozen=True, slots=True)
class ReadPageItem:
    page_label: str
    content: str


@dataclass(frozen=True, slots=True)
class ReadSectionItem:
    resource_id: str
    section_id: str
    section_path: str
    content: str


class DocumentReadError(LookupError):
    """请求目标不可见或结构中不存在时使用的统一读取错误。"""


class DocumentReader:
    """通过一次 active+ACL 快照完成 Page/Section 读取。"""

    def __init__(self, *, snapshots: ActiveDocumentSnapshotLoader) -> None:
        self._snapshots = snapshots

    async def read_pages(
        self,
        resource_id: str,
        page_labels: list[str],
        *,
        scope: PermissionScope,
    ) -> list[ReadPageItem]:
        """读取指定资源的指定页面内容。"""
        documents = await self._snapshots.load_documents([resource_id], scope=scope)
        document = documents.get(resource_id)
        if document is None:
            raise DocumentReadError("document is not visible")

        pages_by_label = {page.page_label: page for page in document.structure.pages}
        pages: list[ReadPageItem] = []
        for label in page_labels:
            page = pages_by_label.get(label)
            if page is None:
                raise DocumentReadError("page is not visible")
            content = document.raw_content[
                page.source_span.start_offset : page.source_span.end_offset
            ]
            pages.append(ReadPageItem(label, content))
        return pages

    async def read_sections(
        self,
        section_ids: list[str],
        *,
        mode: SectionReadMode = SectionReadMode.DIRECT,
        max_depth: int = 1,
        scope: PermissionScope,
    ) -> list[ReadSectionItem]:
        """读取指定 section 的内容，支持直接或递归展开子 section。"""
        locations = await self._snapshots.locate_sections(section_ids, scope=scope)

        items: list[ReadSectionItem] = []
        for section_id in section_ids:
            location = locations.get(section_id)
            if location is None:
                raise DocumentReadError("section is not visible")

            if mode is SectionReadMode.RECURSIVE:
                content = _recursive_content(
                    location.document.raw_content,
                    location.section,
                    location.document.structure.sections,
                    max_depth,
                )
            else:
                content = _direct_content(location.document.raw_content, location.section)

            items.append(
                ReadSectionItem(
                    resource_id=location.document.resource_id,
                    section_id=section_id,
                    section_path=" > ".join(location.section.section_path),
                    content=content,
                )
            )
        return items


def _direct_content(raw_content: str, section: Section) -> str:
    """直接拼接 section 的 content_spans 内容，去除每段前后空白。"""
    parts = [
        raw_content[span.start_offset : span.end_offset].strip()
        for span in section.content_spans
    ]
    return "\n\n".join(part for part in parts if part)


def _recursive_content(
    raw_content: str,
    root: Section,
    sections: list[Section],
    max_depth: int,
) -> str:
    """递归展开 section 及其子 section，按层级用 Markdown 标题表示。"""
    # 构建父子关系映射
    children_by_parent: dict[str | None, list[Section]] = {}
    for section in sections:
        children_by_parent.setdefault(section.parent_section_id, []).append(section)
    # 按 ordinal 排序子节点
    for children in children_by_parent.values():
        children.sort(key=lambda s: s.ordinal)

    parts: list[str] = []

    def visit(section: Section, depth: int) -> None:
        if depth > max_depth:
            return
        # 标题等级至少为 1
        level = max(1, section.level)
        parts.append("#" * level + " " + section.title.strip())
        body = _direct_content(raw_content, section)
        if body:
            parts.append(body)
        for child in children_by_parent.get(section.section_id, []):
            visit(child, depth + 1)

    visit(root, 0)
    return "\n\n".join(parts)