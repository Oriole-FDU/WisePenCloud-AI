from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

from .ooxml import attr, child, children, int_value
from .styles import Styles


class Numbering:
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
        num_props = child(props, "w", "numPr")
        num_id = attr(child(num_props, "w", "numId"), "w", "val")
        level = int_value(attr(child(num_props, "w", "ilvl"), "w", "val"))
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
        for abstract in children(root, "w", "abstractNum"):
            abstract_id = attr(abstract, "w", "abstractNumId")
            if not abstract_id:
                continue
            for level in children(abstract, "w", "lvl"):
                index = int_value(attr(level, "w", "ilvl"), 0) or 0
                self._levels[(abstract_id, index)] = {
                    "format": attr(child(level, "w", "numFmt"), "w", "val") or "decimal",
                    "text": attr(child(level, "w", "lvlText"), "w", "val"),
                    "start": int_value(attr(child(level, "w", "start"), "w", "val"), 1) or 1,
                }
        for num in children(root, "w", "num"):
            num_id = attr(num, "w", "numId")
            abstract_id = attr(child(num, "w", "abstractNumId"), "w", "val")
            if not num_id or not abstract_id:
                continue
            self._nums[num_id] = abstract_id
            for override in children(num, "w", "lvlOverride"):
                # 文档实例可以覆盖某一级的起始值，优先于抽象编号定义。
                level = int_value(attr(override, "w", "ilvl"), 0) or 0
                start = int_value(attr(child(override, "w", "startOverride"), "w", "val"))
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
