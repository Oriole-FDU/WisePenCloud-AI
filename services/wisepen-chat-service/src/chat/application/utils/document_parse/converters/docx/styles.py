from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

from .ooxml import attr, child, children, int_value

Style = dict[str, str | int | None]
Styles = dict[str, Style]
_HEADING_RE = re.compile(r"^(heading|标题)\s*([1-6])$", re.IGNORECASE)


def read_styles(archive: zipfile.ZipFile) -> Styles:
    if "word/styles.xml" not in archive.namelist():
        return {}

    root = ET.fromstring(archive.read("word/styles.xml"))
    styles: Styles = {}
    # 只保存后续渲染需要的段落样式字段，避免把整棵样式 XML 带入渲染阶段。
    for style in children(root, "w", "style"):
        style_id = attr(style, "w", "styleId")
        if not style_id:
            continue
        props = child(style, "w", "pPr")
        num_props = child(props, "w", "numPr")
        styles[style_id] = {
            "name": attr(child(style, "w", "name"), "w", "val"),
            "based_on": attr(child(style, "w", "basedOn"), "w", "val"),
            "outline": int_value(attr(child(props, "w", "outlineLvl"), "w", "val")),
            "num_id": attr(child(num_props, "w", "numId"), "w", "val"),
            "level": int_value(attr(child(num_props, "w", "ilvl"), "w", "val"), 0),
        }
    return styles


def heading_level(styles: Styles, style_id: str | None) -> int | None:
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
