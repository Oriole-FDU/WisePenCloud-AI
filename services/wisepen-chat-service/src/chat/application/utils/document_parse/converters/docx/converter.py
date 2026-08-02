from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .inline import InlineRenderer
from .notes import read_notes, render_notes
from .numbering import Numbering
from .ooxml import attr, child, page_break_count, q
from .styles import Styles, heading_level, read_styles
from .tables import render_table


class DocxConverter:
    """按 Word 正文 block 顺序渲染 DOCX，并插入项目 page marker。"""

    def convert(
        self,
        file_path: str | Path,
        *,
        image_path: str | Path | None = None,
    ) -> str:
        file_path = Path(file_path)
        if not file_path.is_file():
            raise FileNotFoundError(file_path)

        # DOCX 是 ZIP 包；正文、样式、编号和关系信息分别存放在独立 XML 部件中。
        with zipfile.ZipFile(file_path) as archive:
            notes = read_notes(archive)
            relationships = self._read_relationships(archive, "word/document.xml")
            styles = read_styles(archive)
            numbering = Numbering(archive)
            inline = InlineRenderer(
                archive,
                relationships,
                Path(image_path) if image_path is not None else None,
                notes,
            )
            pages = self._render_pages(archive, inline, styles, numbering)

        # 页面标记放在正文之前，供后续解析流程定位原始 Word 页码。
        markdown = "\n\n".join(
            f"<!-- page {index} -->\n\n{page}" if page else f"<!-- page {index} -->"
            for index, page in enumerate(pages, start=1)
        )
        # 脚注和尾注不混入正文顺序，统一在文档末尾输出 Markdown 定义。
        notes_markdown = render_notes(notes)
        return markdown + notes_markdown

    def _render_pages(
        self,
        archive: zipfile.ZipFile,
        inline: InlineRenderer,
        styles: Styles,
        numbering: Numbering,
    ) -> list[str]:
        root = ET.fromstring(archive.read("word/document.xml"))
        body = child(root, "w", "body")
        pages: list[str] = []
        current: list[str] = []

        # 只按 w:body 的直接子节点遍历，保留段落和表格的原始交错顺序。
        for node in self._iter_body_nodes(body):
            if node.tag == q("w", "p"):
                text, breaks = self._paragraph(node, inline, styles, numbering)
                fragments = text.split("\f")
                # 内联渲染把分页符统一为换页符，先拆出正文页，再处理段落属性中的分页。
                for fragment_index, fragment in enumerate(fragments):
                    if fragment:
                        current.append(fragment)
                    if fragment_index < len(fragments) - 1:
                        pages.append("\n\n".join(current))
                        current = []
                if breaks:
                    pages.append("\n\n".join(current))
                    current = []
            elif node.tag == q("w", "tbl"):
                table, breaks = render_table(node, inline)
                if table:
                    current.append(table)
                if breaks:
                    pages.append("\n\n".join(current))
                    current = []
            elif node.tag == q("w", "sectPr"):
                if current:
                    pages.append("\n\n".join(current))
                    current = []

        if current or not pages:
            pages.append("\n\n".join(current))
        return pages

    def _paragraph(
        self,
        paragraph: ET.Element,
        inline: InlineRenderer,
        styles: Styles,
        numbering: Numbering,
    ) -> tuple[str, int]:
        text = inline.render(paragraph).strip()
        explicit_breaks = text.count("\f")
        props = child(paragraph, "w", "pPr")
        style_id = attr(child(props, "w", "pStyle"), "w", "val")
        level = heading_level(styles, style_id)
        label = numbering.label(props, style_id, styles)
        if level is not None:
            rendered = f"{'#' * level} {label}{text}" if text else ""
        else:
            rendered = f"{label}{text}" if text else ""
        # 运行内容中的分页已在上层拆分，这里只补充尚未被内联文本体现的分页。
        return rendered, max(0, page_break_count(paragraph) - explicit_breaks) + int(
            child(props, "w", "sectPr") is not None
        )

    @staticmethod
    def _iter_body_nodes(body: ET.Element | None):
        if body is None:
            return
        for node in body:
            if node.tag != q("w", "sdt"):
                yield node
                continue
            # 内容控件本身不是正文 block，展开其内容后继续参与原有顺序。
            content = child(node, "w", "sdtContent")
            if content is not None:
                yield from content

    @staticmethod
    def _read_relationships(
        archive: zipfile.ZipFile,
        source_part: str,
    ) -> dict[str, str]:
        rels_path = DocxConverter._rels_path(source_part)
        if rels_path not in archive.namelist():
            return {}
        root = ET.fromstring(archive.read(rels_path))
        relationships: dict[str, str] = {}
        for rel in root:
            if rel.tag != q("rel", "Relationship"):
                continue
            rel_id, target = rel.get("Id"), rel.get("Target")
            if not rel_id or not target:
                continue
            if rel.get("TargetMode") == "External":
                # 外部链接直接保留 URL；包内资源则转换为 ZIP 中的规范路径。
                relationships[rel_id] = target
            elif target.startswith("/"):
                relationships[rel_id] = posixpath.normpath(target[1:])
            else:
                relationships[rel_id] = posixpath.normpath(
                    posixpath.join(posixpath.dirname(source_part), target)
                )
        return relationships

    @staticmethod
    def _rels_path(source_part: str) -> str:
        parent, name = posixpath.dirname(source_part), posixpath.basename(source_part)
        # OOXML 关系文件固定放在源部件同级的 _rels 目录下。
        return f"{parent}/_rels/{name}.rels" if parent else f"_rels/{name}.rels"
