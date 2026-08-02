from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

from .ooxml import attr, children, local_name


def read_notes(archive: zipfile.ZipFile) -> dict[tuple[str, str], dict[str, str]]:
    notes: dict[tuple[str, str], dict[str, str]] = {}
    for kind, filename, element_name in (
        ("footnote", "word/footnotes.xml", "footnote"),
        ("endnote", "word/endnotes.xml", "endnote"),
    ):
        if filename not in archive.namelist():
            continue
        root = ET.fromstring(archive.read(filename))
        for note in children(root, "w", element_name):
            # 分隔线节点不是用户脚注内容，不能生成可见的引用定义。
            if attr(note, "w", "type") in {"separator", "continuationSeparator"}:
                continue
            note_id = attr(note, "w", "id")
            if note_id is None:
                continue
            text = "".join(
                item.text or ""
                for item in note.iter()
                if local_name(item.tag) in {"t", "delText"} and item.text
            ).strip()
            if text:
                # 读取阶段区分脚注和尾注，避免相同编号的节点互相覆盖。
                notes[(kind, note_id)] = {"id": note_id, "text": text}
    return notes


def render_notes(notes: dict[tuple[str, str], dict[str, str]]) -> str:
    if not notes:
        return ""
    # 定义集中放在正文之后，正文中的引用只需保留 [^id] 标记。
    lines = ["", "", "## Notes"]
    for (kind, note_id), note in notes.items():
        label = "Footnote" if kind == "footnote" else "Endnote"
        lines.append(f"[^{note_id}]: {label} {note['text']}")
    return "\n".join(lines)
