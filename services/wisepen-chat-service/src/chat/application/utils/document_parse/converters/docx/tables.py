from __future__ import annotations

from xml.etree import ElementTree as ET

from .inline import InlineRenderer
from .ooxml import attr, child, children, int_value, page_break_count, q


def render_table(table: ET.Element, inline: InlineRenderer) -> tuple[str, int]:
    rows = list(children(table, "w", "tr"))
    if not rows:
        return "", 0
    # 合并单元格和嵌套表格无法用 Markdown 管道表准确表达，改用 HTML 保留结构。
    complex_table = (
        table.find(f".//{q('w', 'gridSpan')}") is not None
        or table.find(f".//{q('w', 'vMerge')}") is not None
        or any(child(cell, "w", "tbl") is not None for cell in table.iter(q("w", "tc")))
    )
    page_breaks = sum(page_break_count(row) for row in rows)
    return (
        render_html_table(rows, inline) if complex_table else render_pipe_table(rows, inline),
        page_breaks,
    )


def render_pipe_table(rows: list[ET.Element], inline: InlineRenderer) -> str:
    values = [[cell_text(cell, inline) for cell in children(row, "w", "tc")] for row in rows]
    width = max(len(row) for row in values)
    # 管道表要求每行列数一致，缺少的单元格用空值补齐。
    values = [row + [""] * (width - len(row)) for row in values]
    lines = [
        "| " + " | ".join(_escape(cell) for cell in values[0]) + " |",
        "| " + " | ".join("---" for _ in values[0]) + " |",
    ]
    lines.extend("| " + " | ".join(_escape(cell) for cell in row) + " |" for row in values[1:])
    return "\n".join(lines)


def render_html_table(rows: list[ET.Element], inline: InlineRenderer) -> str:
    lines = ["<table>"]
    for row_index, row in enumerate(rows):
        lines.append("<tr>")
        for cell_index, cell in enumerate(children(row, "w", "tc")):
            props = child(cell, "w", "tcPr")
            colspan = int_value(attr(child(props, "w", "gridSpan"), "w", "val"), 1) or 1
            merge = child(props, "w", "vMerge")
            attrs: list[str] = []
            if colspan > 1:
                attrs.append(f' colspan="{colspan}"')
            merge_value = attr(merge, "w", "val") if merge is not None else None
            if merge_value == "restart":
                # 从 restart 单元格向下统计连续 vMerge，生成 HTML rowspan。
                rowspan = 1
                for next_row in rows[row_index + 1:]:
                    next_cells = list(children(next_row, "w", "tc"))
                    if cell_index >= len(next_cells):
                        break
                    next_props = child(next_cells[cell_index], "w", "tcPr")
                    next_merge = child(next_props, "w", "vMerge")
                    if next_merge is None:
                        break
                    if attr(next_merge, "w", "val") not in {None, "continue"}:
                        break
                    rowspan += 1
                if rowspan > 1:
                    attrs.append(f' rowspan="{rowspan}"')
            elif merge_value in {None, "continue"} and merge is not None:
                attrs.append(' data-vmerge="continue"')
                if merge_value in {None, "continue"}:
                    # 合并区域的后续单元格由 rowspan 表示，不能再次输出。
                    continue
            lines.append(f"<td{''.join(attrs)}>{cell_text(cell, inline)}</td>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def cell_text(cell: ET.Element, inline: InlineRenderer) -> str:
    parts: list[str] = []
    for node in cell:
        if node.tag == q("w", "p"):
            text = inline.render(node).strip()
            if text:
                parts.append(text)
        elif node.tag == q("w", "tbl"):
            # 嵌套表格作为单元格内容保留，并与同一单元格中的段落换行。
            nested, _ = render_table(node, inline)
            if nested:
                parts.append(nested)
    return "<br>".join(parts)


def _escape(value: str) -> str:
    # 先转义反斜杠和管道符，避免单元格内容破坏 Markdown 表格语法。
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")
