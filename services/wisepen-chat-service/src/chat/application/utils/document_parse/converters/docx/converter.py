from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Iterable
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

# --- OOXML 命名空间和正则常量 ---

NS = {
    # DOCX 各 XML 部件共用这些命名空间，统一通过前缀构造完整标签。
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}

Style = dict[str, str | int | None]
Styles = dict[str, Style]

_HEADING_RE = re.compile(r"^(heading|标题)\s*([1-6])$", re.IGNORECASE)
_HYPERLINK_FIELD = re.compile(r'HYPERLINK\s+"([^"]+)"', re.IGNORECASE)
_OPERATORS = {
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "∬": r"\iint",
    "∭": r"\iiint",
    "∮": r"\oint",
}


# --- DOCX 包读取和正文分页渲染 ---

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
            notes = _read_notes(archive)
            relationships = self._read_relationships(archive, "word/document.xml")
            styles = _read_styles(archive)
            numbering = _Numbering(archive)
            inline = _InlineRenderer(
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
        notes_markdown = _render_notes(notes)
        return markdown + notes_markdown

    def _render_pages(
        self,
        archive: zipfile.ZipFile,
        inline: _InlineRenderer,
        styles: Styles,
        numbering: _Numbering,
    ) -> list[str]:
        root = ET.fromstring(archive.read("word/document.xml"))
        body = _child(root, "w", "body")
        pages: list[str] = []
        current: list[str] = []

        # 只按 w:body 的直接子节点遍历，保留段落和表格的原始交错顺序。
        for node in self._iter_body_nodes(body):
            if node.tag == _q("w", "p"):
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
            elif node.tag == _q("w", "tbl"):
                table, breaks = _render_table(node, inline)
                if table:
                    current.append(table)
                if breaks:
                    pages.append("\n\n".join(current))
                    current = []
            elif node.tag == _q("w", "sectPr"):
                if current:
                    pages.append("\n\n".join(current))
                    current = []

        if current or not pages:
            pages.append("\n\n".join(current))
        return pages

    def _paragraph(
        self,
        paragraph: ET.Element,
        inline: _InlineRenderer,
        styles: Styles,
        numbering: _Numbering,
    ) -> tuple[str, int]:
        text = inline.render(paragraph).strip(" \t\r\n")
        explicit_breaks = text.count("\f")
        props = _child(paragraph, "w", "pPr")
        visible_text = text.replace("\f", "").strip()
        if not visible_text and explicit_breaks and _child(props, "w", "sectPr") is None:
            # 空段落中的分页符只是排版控制符，没有可回源的正文锚点。
            return "", 0
        style_id = _attr(_child(props, "w", "pStyle"), "w", "val")
        level = _heading_level(styles, style_id)
        label = numbering.label(props, style_id, styles)
        if level is not None:
            rendered = f"{'#' * level} {label}{text}" if text else ""
        else:
            rendered = f"{label}{text}" if text else ""
        # 运行内容中的分页已在上层拆分，这里只补充尚未被内联文本体现的分页。
        return rendered, max(0, _page_break_count(paragraph) - explicit_breaks) + int(
            _child(props, "w", "sectPr") is not None
        )

    @staticmethod
    def _iter_body_nodes(body: ET.Element | None):
        if body is None:
            return
        for node in body:
            if node.tag != _q("w", "sdt"):
                yield node
                continue
            # 内容控件本身不是正文 block，展开其内容后继续参与原有顺序。
            content = _child(node, "w", "sdtContent")
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
            if rel.tag != _q("rel", "Relationship"):
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


# --- 段落内联内容渲染 ---

class _InlineRenderer:
    """将段落中的 OOXML 内联节点转换为 Markdown 片段。"""

    __slots__ = ("archive", "image_path", "relationships", "notes")

    def __init__(
        self,
        archive: zipfile.ZipFile,
        relationships: dict[str, str],
        image_path: Path | None,
        notes: dict[tuple[str, str], dict[str, str]],
    ) -> None:
        self.archive = archive
        self.relationships = relationships
        self.image_path = image_path
        self.notes = notes

    def render(self, node: ET.Element) -> str:
        parts: list[str] = []
        field_url: str | None = None
        field_text: list[str] = []
        for item in node:
            name = _local_name(item.tag)
            if name == "r":
                instruction = "".join(
                    child.text or "" for child in item if _local_name(child.tag) == "instrText"
                )
                field_char = next(
                    (child for child in item if _local_name(child.tag) == "fldChar"),
                    None,
                )
                if field_char is not None:
                    field_type = _attr(field_char, "w", "fldCharType")
                    if field_type == "begin":
                        # HYPERLINK 域的地址和显示文本分属域指令与后续 run。
                        field_url, field_text = None, []
                        continue
                    if field_type == "separate":
                        continue
                    if field_type == "end":
                        # 域结束时统一组装链接；没有地址的域仍保留其显示文本。
                        if field_url and field_text:
                            parts.append(self._link("".join(field_text), field_url))
                        elif field_text:
                            parts.extend(field_text)
                        field_url, field_text = None, []
                        continue
                if instruction:
                    match = _HYPERLINK_FIELD.search(instruction)
                    if match:
                        field_url = match.group(1)
                    continue
                text = self.render(item)
                if field_url is not None:
                    field_text.append(text)
                else:
                    parts.append(text)
            elif name in {"t", "delText"}:
                parts.append(item.text or "")
            elif name == "tab":
                parts.append("\t")
            elif name in {"br", "cr"}:
                # Markdown 正文需要换行，分页则使用内部换页符交给页面渲染阶段处理。
                parts.append(
                    "\f"
                    if name == "br" and _attr(item, "w", "type") == "page"
                    else "\n"
                )
            elif name == "lastRenderedPageBreak":
                parts.append("\f")
            elif name == "hyperlink":
                label = self.render(item)
                target = self.relationships.get(_attr(item, "r", "id") or "")
                anchor = _attr(item, "w", "anchor")
                href = target if target else f"#{anchor}" if anchor else ""
                parts.append(self._link(label, href))
            elif name in {"oMath", "oMathPara"}:
                formula = _to_latex(item)
                if formula:
                    parts.append(f"${formula}$")
            elif name == "drawing":
                image = self._image(item)
                if image:
                    parts.append(image)
                textbox = self._textbox(item)
                if textbox:
                    parts.append(f"\n\n> {textbox}")
            elif name == "pict":
                textbox = self._textbox(item)
                if textbox:
                    parts.append(f"\n\n> {textbox}")
            elif name in {"sdt", "sdtContent", "smartTag"}:
                parts.append(self.render(item))
            elif name in {"footnoteReference", "endnoteReference"}:
                # 已解析到的脚注/尾注使用 Markdown 引用，缺少定义时保留显式占位标记。
                note_id = _attr(item, "w", "id") or ""
                note = self.notes.get(
                    ("footnote" if name == "footnoteReference" else "endnote", note_id),
                    {},
                )
                parts.append(f"[^{note_id}]" if note else f"<{name[:-9]}ref id={escape(note_id)} />")
            elif name == "commentReference":
                parts.append(f"<comment-ref id={escape(_attr(item, 'w', 'id') or '')} />")
            else:
                parts.append(self.render(item))
        return "".join(parts)

    def _image(self, drawing: ET.Element) -> str:
        blip = next((item for item in drawing.iter() if _local_name(item.tag) == "blip"), None)
        target = self.relationships.get(
            _attr(blip, "r", "embed") or _attr(blip, "r", "link") or ""
        )
        if not target or target.startswith(("http://", "https://")):
            return "" if not target else f"![image]({target})"
        # 包内图片从 ZIP 解压到调用方目录；没有目录时保留原始包内路径。
        image_name = Path(target).name
        doc_pr = next(
            (item for item in drawing.iter() if _local_name(item.tag) == "docPr"),
            None,
        )
        alt = (
            (doc_pr.get("descr") or doc_pr.get("title"))
            if doc_pr is not None
            else None
        ) or image_name
        if self.image_path is not None:
            self.image_path.mkdir(parents=True, exist_ok=True)
            (self.image_path / image_name).write_bytes(self.archive.read(target))
            target = f"{self.image_path.name}/{image_name}"
        return f"\n\n![{alt}]({target})\n\n"

    @staticmethod
    def _textbox(node: ET.Element) -> str:
        texts = [
            item.text or ""
            for item in node.iter()
            if _local_name(item.tag) == "t" and item.text
        ]
        return "".join(texts).strip()

    @staticmethod
    def _link(label: str, target: str) -> str:
        if not label:
            return ""
        # 没有目标地址时返回纯文本，避免生成无效的 Markdown 链接。
        return f"[{label}]({target})" if target else label


# --- Word 编号定义读取和列表前缀生成 ---

class _Numbering:
    """读取 Word 编号定义，并按文档出现顺序生成列表前缀。"""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self._levels: dict[tuple[str, int], dict[str, object]] = {}
        self._nums: dict[str, str] = {}
        self._starts: dict[tuple[str, int], int] = {}
        self._counters: dict[str, dict[int, int]] = {}
        if "word/numbering.xml" in archive.namelist():
            self._read(archive)

    def label(
        self,
        props: ET.Element | None,
        style_id: str | None,
        styles: Styles,
    ) -> str:
        num_props = _child(props, "w", "numPr")
        num_id = _attr(_child(num_props, "w", "numId"), "w", "val")
        level = _int_value(_attr(_child(num_props, "w", "ilvl"), "w", "val"))
        if num_id is None and style_id:
            # 段落未显式声明编号时，继承段落样式中的编号配置。
            style = styles.get(style_id, {})
            num_id = style.get("num_id") if isinstance(style.get("num_id"), str) else None
            level = style.get("level") if isinstance(style.get("level"), int) else level
        if not num_id or num_id == "0":
            return ""

        level = level or 0
        abstract_id = self._nums.get(num_id, "")
        definition = self._levels.get((abstract_id, level), {})
        fmt = str(definition.get("format", "decimal"))
        text = definition.get("text")
        counters = self._counters.setdefault(num_id, {})
        if level not in counters:
            counters[level] = self._starts.get((num_id, level), int(definition.get("start", 1)))
        else:
            counters[level] += 1
        for deeper in list(counters):
            if deeper > level:
                # 回到上层列表时，清除旧的子层计数，避免下一项沿用错误编号。
                del counters[deeper]

        if fmt == "bullet":
            return "  " * level + "- "
        value = _format_number(counters[level], fmt)
        if isinstance(text, str):
            value = self._render_template(abstract_id, text, counters)
        else:
            value = f"{value}."
        return "  " * level + value + " "

    def _read(self, archive: zipfile.ZipFile) -> None:
        root = ET.fromstring(archive.read("word/numbering.xml"))
        # abstractNum 描述层级格式，num 再把文档实际使用的 numId 映射到它。
        for abstract in _children(root, "w", "abstractNum"):
            abstract_id = _attr(abstract, "w", "abstractNumId")
            if not abstract_id:
                continue
            for level in _children(abstract, "w", "lvl"):
                index = _int_value(_attr(level, "w", "ilvl"), 0) or 0
                self._levels[(abstract_id, index)] = {
                    "format": _attr(_child(level, "w", "numFmt"), "w", "val") or "decimal",
                    "text": _attr(_child(level, "w", "lvlText"), "w", "val"),
                    "start": _int_value(_attr(_child(level, "w", "start"), "w", "val"), 1) or 1,
                }
        for num in _children(root, "w", "num"):
            num_id = _attr(num, "w", "numId")
            abstract_id = _attr(_child(num, "w", "abstractNumId"), "w", "val")
            if not num_id or not abstract_id:
                continue
            self._nums[num_id] = abstract_id
            for override in _children(num, "w", "lvlOverride"):
                # 文档实例可以覆盖某一级的起始值，优先于抽象编号定义。
                level = _int_value(_attr(override, "w", "ilvl"), 0) or 0
                start = _int_value(_attr(_child(override, "w", "startOverride"), "w", "val"))
                if start is not None:
                    self._starts[(num_id, level)] = start

    def _render_template(
        self,
        abstract_id: str,
        template: str,
        counters: dict[int, int],
    ) -> str:
        result = template
        for index in range(1, 10):
            level = index - 1
            definition = self._levels.get((abstract_id, level), {})
            fmt = str(definition.get("format", "decimal"))
            result = result.replace(
                f"%{index}",
                _format_number(counters.get(level, 1), fmt),
            )
        return result


# --- Word 样式读取和标题层级识别 ---

def _read_styles(archive: zipfile.ZipFile) -> Styles:
    if "word/styles.xml" not in archive.namelist():
        return {}

    root = ET.fromstring(archive.read("word/styles.xml"))
    styles: Styles = {}
    # 只保存后续渲染需要的段落样式字段，避免把整棵样式 XML 带入渲染阶段。
    for style in _children(root, "w", "style"):
        style_id = _attr(style, "w", "styleId")
        if not style_id:
            continue
        props = _child(style, "w", "pPr")
        num_props = _child(props, "w", "numPr")
        styles[style_id] = {
            "name": _attr(_child(style, "w", "name"), "w", "val"),
            "based_on": _attr(_child(style, "w", "basedOn"), "w", "val"),
            "outline": _int_value(_attr(_child(props, "w", "outlineLvl"), "w", "val")),
            "num_id": _attr(_child(num_props, "w", "numId"), "w", "val"),
            "level": _int_value(_attr(_child(num_props, "w", "ilvl"), "w", "val"), 0),
        }
    return styles


def _heading_level(styles: Styles, style_id: str | None) -> int | None:
    visited: set[str] = set()
    while style_id and style_id not in visited:
        visited.add(style_id)
        style = styles.get(style_id)
        if style is None:
            return _heading_name_level(style_id)

        outline = style.get("outline")
        if isinstance(outline, int):
            # outlineLvl 从 0 开始，而 Markdown 标题层级从 1 开始。
            return max(1, min(6, outline + 1))
        level = _heading_name_level(style_id) or _heading_name_level(style.get("name"))
        if level is not None:
            return level
        based_on = style.get("based_on")
        # Word 样式可继承父样式；visited 防止异常文档中的循环继承。
        style_id = based_on if isinstance(based_on, str) else None
    return None


def _heading_name_level(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = _HEADING_RE.fullmatch(value.replace("_", " ").strip())
    return int(match.group(2)) if match else None


# --- 普通表格和复杂表格渲染 ---

def _render_table(table: ET.Element, inline: _InlineRenderer) -> tuple[str, int]:
    rows = list(_children(table, "w", "tr"))
    if not rows:
        return "", 0
    # 合并单元格和嵌套表格无法用 Markdown 管道表准确表达，改用 HTML 保留结构。
    complex_table = (
        table.find(f".//{_q('w', 'gridSpan')}") is not None
        or table.find(f".//{_q('w', 'vMerge')}") is not None
        or any(_child(cell, "w", "tbl") is not None for cell in table.iter(_q("w", "tc")))
    )
    page_breaks = sum(_page_break_count(row) for row in rows)
    return (
        _render_html_table(rows, inline) if complex_table else _render_pipe_table(rows, inline),
        page_breaks,
    )


def _render_pipe_table(rows: list[ET.Element], inline: _InlineRenderer) -> str:
    values = [[_cell_text(cell, inline) for cell in _children(row, "w", "tc")] for row in rows]
    width = max(len(row) for row in values)
    # 管道表要求每行列数一致，缺少的单元格用空值补齐。
    values = [row + [""] * (width - len(row)) for row in values]
    lines = [
        "| " + " | ".join(_escape_cell(cell) for cell in values[0]) + " |",
        "| " + " | ".join("---" for _ in values[0]) + " |",
    ]
    lines.extend("| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in values[1:])
    return "\n".join(lines)


def _render_html_table(rows: list[ET.Element], inline: _InlineRenderer) -> str:
    lines = ["<table>"]
    for row_index, row in enumerate(rows):
        lines.append("<tr>")
        for cell_index, cell in enumerate(_children(row, "w", "tc")):
            props = _child(cell, "w", "tcPr")
            colspan = _int_value(_attr(_child(props, "w", "gridSpan"), "w", "val"), 1) or 1
            merge = _child(props, "w", "vMerge")
            attrs: list[str] = []
            if colspan > 1:
                attrs.append(f' colspan="{colspan}"')
            merge_value = _attr(merge, "w", "val") if merge is not None else None
            if merge_value == "restart":
                # 从 restart 单元格向下统计连续 vMerge，生成 HTML rowspan。
                rowspan = 1
                for next_row in rows[row_index + 1:]:
                    next_cells = list(_children(next_row, "w", "tc"))
                    if cell_index >= len(next_cells):
                        break
                    next_props = _child(next_cells[cell_index], "w", "tcPr")
                    next_merge = _child(next_props, "w", "vMerge")
                    if next_merge is None:
                        break
                    if _attr(next_merge, "w", "val") not in {None, "continue"}:
                        break
                    rowspan += 1
                if rowspan > 1:
                    attrs.append(f' rowspan="{rowspan}"')
            elif merge_value in {None, "continue"} and merge is not None:
                attrs.append(' data-vmerge="continue"')
                if merge_value in {None, "continue"}:
                    # 合并区域的后续单元格由 rowspan 表示，不能再次输出。
                    continue
            lines.append(f"<td{''.join(attrs)}>{_cell_text(cell, inline)}</td>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _cell_text(cell: ET.Element, inline: _InlineRenderer) -> str:
    parts: list[str] = []
    for node in cell:
        if node.tag == _q("w", "p"):
            text = inline.render(node).strip()
            if text:
                parts.append(text)
        elif node.tag == _q("w", "tbl"):
            # 嵌套表格作为单元格内容保留，并与同一单元格中的段落换行。
            nested, _ = _render_table(node, inline)
            if nested:
                parts.append(nested)
    return "<br>".join(parts)


# --- 脚注和尾注读取输出 ---

def _read_notes(archive: zipfile.ZipFile) -> dict[tuple[str, str], dict[str, str]]:
    notes: dict[tuple[str, str], dict[str, str]] = {}
    for kind, filename, element_name in (
        ("footnote", "word/footnotes.xml", "footnote"),
        ("endnote", "word/endnotes.xml", "endnote"),
    ):
        if filename not in archive.namelist():
            continue
        root = ET.fromstring(archive.read(filename))
        for note in _children(root, "w", element_name):
            # 分隔线节点不是用户脚注内容，不能生成可见的引用定义。
            if _attr(note, "w", "type") in {"separator", "continuationSeparator"}:
                continue
            note_id = _attr(note, "w", "id")
            if note_id is None:
                continue
            text = "".join(
                item.text or ""
                for item in note.iter()
                if _local_name(item.tag) in {"t", "delText"} and item.text
            ).strip()
            if text:
                # 读取阶段区分脚注和尾注，避免相同编号的节点互相覆盖。
                notes[(kind, note_id)] = {"id": note_id, "text": text}
    return notes


def _render_notes(notes: dict[tuple[str, str], dict[str, str]]) -> str:
    if not notes:
        return ""
    # 定义集中放在正文之后，正文中的引用只需保留 [^id] 标记。
    lines = ["", "", "## Notes"]
    for (kind, note_id), note in notes.items():
        label = "Footnote" if kind == "footnote" else "Endnote"
        lines.append(f"[^{note_id}]: {label} {note['text']}")
    return "\n".join(lines)


# --- OMML 公式转 LaTeX ---

def _to_latex(node: ET.Element) -> str:
    # Word 公式使用 OMML；这里只转换常见结构，未知节点继续递归读取其文本。
    return "".join(_convert_formula(item) for item in node)


def _convert_formula(node: ET.Element) -> str:
    name = _local_name(node.tag)
    if name in {"r", "t"}:
        return "".join(item.text or "" for item in node.iter() if _local_name(item.tag) == "t")
    if name in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "fName"}:
        return "".join(_convert_formula(item) for item in node)
    if name == "f":
        return rf"\frac{{{_named_formula_part(node, 'num')}}}{{{_named_formula_part(node, 'den')}}}"
    if name == "rad":
        degree = _named_formula_part(node, "deg")
        value = _named_formula_part(node, "e")
        return rf"\sqrt[{degree}]{{{value}}}" if degree else rf"\sqrt{{{value}}}"
    if name == "sSub":
        return f"{_group_formula(_named_formula_part(node, 'e'))}_{{{_named_formula_part(node, 'sub')}}}"
    if name == "sSup":
        return f"{_group_formula(_named_formula_part(node, 'e'))}^{{{_named_formula_part(node, 'sup')}}}"
    if name == "sSubSup":
        return (
            f"{_group_formula(_named_formula_part(node, 'e'))}"
            f"_{{{_named_formula_part(node, 'sub')}}}^{{{_named_formula_part(node, 'sup')}}}"
        )
    if name == "nary":
        # OMML 的积分、求和等运算符通过 m:chr 指定，缺省按积分处理。
        operator = _OPERATORS.get(_attr(_child(node, "m", "chr"), "m", "val") or "", r"\int")
        sub = _named_formula_part(node, "sub")
        sup = _named_formula_part(node, "sup")
        limits = f"_{{{sub}}}" if sub else ""
        limits += f"^{{{sup}}}" if sup else ""
        return f"{operator}{limits} {_named_formula_part(node, 'e')}"
    if name == "func":
        function = _named_formula_part(node, "fName").strip()
        value = _named_formula_part(node, "e")
        return rf"\{function}{{{value}}}" if function else value
    if name == "d":
        return rf"\left( {_named_formula_part(node, 'e')} \right)"
    if name == "m":
        # 矩阵行列使用 LaTeX 的 & 和换行分隔，保留 Word 中的二维结构。
        rows = []
        for row in _children(node, "m", "mr"):
            rows.append(" & ".join(_convert_formula(item) for item in _children(row, "m", "e")))
        return r"\begin{matrix} " + r" \\ ".join(rows) + r" \end{matrix}"
    if name.endswith("Pr") or name in {"ctrlPr", "rPr"}:
        return ""
    return "".join(_convert_formula(item) for item in node)


def _named_formula_part(node: ET.Element, name: str) -> str:
    target = _child(node, "m", name)
    return _convert_formula(target) if target is not None else ""


def _group_formula(value: str) -> str:
    # 单字符可直接加上下标，多字符或命令必须整体加花括号。
    return value if len(value) == 1 and "\\" not in value else f"{{{value}}}"


def _format_number(value: int, fmt: str) -> str:
    if fmt == "decimalZero":
        return f"{value:02d}"
    if fmt in {"lowerLetter", "upperLetter"}:
        # 字母编号采用类似 Excel 列名的 1-based 进位规则：1 -> a，27 -> aa。
        result = ""
        while value:
            value, remainder = divmod(value - 1, 26)
            result = chr(ord("a") + remainder) + result
        return result.upper() if fmt == "upperLetter" else result
    if fmt in {"lowerRoman", "upperRoman"}:
        # Word 的罗马数字格式只需覆盖常用的正整数编号。
        pairs = (
            (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
        )
        result = []
        remaining = value
        for amount, glyph in pairs:
            while remaining >= amount:
                result.append(glyph)
                remaining -= amount
        roman = "".join(result)
        return roman if fmt == "upperRoman" else roman.lower()
    return str(value)


# --- OOXML 节点访问辅助函数 ---

def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def _attr(node: ET.Element | None, prefix: str, name: str) -> str | None:
    return None if node is None else node.get(_q(prefix, name))


def _child(node: ET.Element | None, prefix: str, name: str) -> ET.Element | None:
    return None if node is None else node.find(_q(prefix, name))


def _children(node: ET.Element | None, prefix: str, name: str) -> Iterable[ET.Element]:
    return () if node is None else node.findall(_q(prefix, name))


def _local_name(tag: str) -> str:
    # 内联节点来自多个命名空间，读取局部名可复用同一套分支逻辑。
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _int_value(value: str | None, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        # 文档可能包含非法或非数字属性，按调用方给出的默认值继续解析。
        return default


def _page_break_count(node: ET.Element) -> int:
    # Word 同时使用显式 w:br 和排版结果 w:lastRenderedPageBreak 表示分页。
    return sum(
        1
        for item in node.iter()
        if (
            item.tag == _q("w", "lastRenderedPageBreak")
            or (
                item.tag == _q("w", "br")
                and _attr(item, "w", "type") == "page"
            )
        )
    )


def _escape_cell(value: str) -> str:
    # 先转义反斜杠和管道符，避免单元格内容破坏 Markdown 表格语法。
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")
