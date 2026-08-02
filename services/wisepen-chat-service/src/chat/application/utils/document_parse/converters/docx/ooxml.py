from __future__ import annotations

from collections.abc import Iterable
from xml.etree import ElementTree as ET

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


def q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def attr(node: ET.Element | None, prefix: str, name: str) -> str | None:
    return None if node is None else node.get(q(prefix, name))


def child(node: ET.Element | None, prefix: str, name: str) -> ET.Element | None:
    return None if node is None else node.find(q(prefix, name))


def children(node: ET.Element | None, prefix: str, name: str) -> Iterable[ET.Element]:
    return () if node is None else node.findall(q(prefix, name))


def local_name(tag: str) -> str:
    # 内联节点来自多个命名空间，读取局部名可复用同一套分支逻辑。
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def int_value(value: str | None, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        # 文档可能包含非法或非数字属性，按调用方给出的默认值继续解析。
        return default


def page_break_count(node: ET.Element) -> int:
    # Word 同时使用显式 w:br 和排版结果 w:lastRenderedPageBreak 表示分页。
    return sum(
        1
        for item in node.iter()
        if (
            item.tag == q("w", "lastRenderedPageBreak")
            or (
                item.tag == q("w", "br")
                and attr(item, "w", "type") == "page"
            )
        )
    )
