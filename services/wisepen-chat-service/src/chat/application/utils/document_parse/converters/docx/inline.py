from __future__ import annotations

import re
import zipfile
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from .formula import to_latex
from .ooxml import attr, local_name

_HYPERLINK_FIELD = re.compile(r'HYPERLINK\s+"([^"]+)"', re.IGNORECASE)


class InlineRenderer:
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
            name = local_name(item.tag)
            if name == "r":
                instruction = "".join(
                    child.text or "" for child in item if local_name(child.tag) == "instrText"
                )
                field_char = next(
                    (child for child in item if local_name(child.tag) == "fldChar"),
                    None,
                )
                if field_char is not None:
                    field_type = attr(field_char, "w", "fldCharType")
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
                    if name == "br" and attr(item, "w", "type") == "page"
                    else "\n"
                )
            elif name == "lastRenderedPageBreak":
                parts.append("\f")
            elif name == "hyperlink":
                label = self.render(item)
                target = self.relationships.get(attr(item, "r", "id") or "")
                anchor = attr(item, "w", "anchor")
                href = target if target else f"#{anchor}" if anchor else ""
                parts.append(self._link(label, href))
            elif name in {"oMath", "oMathPara"}:
                formula = to_latex(item)
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
                note_id = attr(item, "w", "id") or ""
                note = self.notes.get(
                    ("footnote" if name == "footnoteReference" else "endnote", note_id),
                    {},
                )
                parts.append(f"[^{note_id}]" if note else f"<{name[:-9]}ref id={escape(note_id)} />")
            elif name == "commentReference":
                parts.append(f"<comment-ref id={escape(attr(item, 'w', 'id') or '')} />")
            else:
                parts.append(self.render(item))
        return "".join(parts)

    def _image(self, drawing: ET.Element) -> str:
        blip = next((item for item in drawing.iter() if local_name(item.tag) == "blip"), None)
        target = self.relationships.get(
            attr(blip, "r", "embed") or attr(blip, "r", "link") or ""
        )
        if not target or target.startswith(("http://", "https://")):
            return "" if not target else f"![image]({target})"
        # 包内图片从 ZIP 解压到调用方目录；没有目录时保留原始包内路径。
        image_name = Path(target).name
        doc_pr = next(
            (item for item in drawing.iter() if local_name(item.tag) == "docPr"),
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
            if local_name(item.tag) == "t" and item.text
        ]
        return "".join(texts).strip()

    @staticmethod
    def _link(label: str, target: str) -> str:
        if not label:
            return ""
        # 没有目标地址时返回纯文本，避免生成无效的 Markdown 链接。
        return f"[{label}]({target})" if target else label
