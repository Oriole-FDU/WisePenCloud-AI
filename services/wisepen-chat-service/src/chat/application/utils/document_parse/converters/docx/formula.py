from __future__ import annotations

from xml.etree import ElementTree as ET

from .ooxml import attr, child, children, local_name

_OPERATORS = {
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "∬": r"\iint",
    "∭": r"\iiint",
    "∮": r"\oint",
}


def to_latex(node: ET.Element) -> str:
    # Word 公式使用 OMML；这里只转换常见结构，未知节点继续递归读取其文本。
    return "".join(_convert(item) for item in node)


def _convert(node: ET.Element) -> str:
    name = local_name(node.tag)
    if name in {"r", "t"}:
        return "".join(item.text or "" for item in node.iter() if local_name(item.tag) == "t")
    if name in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "fName"}:
        return "".join(_convert(item) for item in node)
    if name == "f":
        return rf"\frac{{{_named(node, 'num')}}}{{{_named(node, 'den')}}}"
    if name == "rad":
        degree = _named(node, "deg")
        value = _named(node, "e")
        return rf"\sqrt[{degree}]{{{value}}}" if degree else rf"\sqrt{{{value}}}"
    if name == "sSub":
        return f"{_group(_named(node, 'e'))}_{{{_named(node, 'sub')}}}"
    if name == "sSup":
        return f"{_group(_named(node, 'e'))}^{{{_named(node, 'sup')}}}"
    if name == "sSubSup":
        return (
            f"{_group(_named(node, 'e'))}"
            f"_{{{_named(node, 'sub')}}}^{{{_named(node, 'sup')}}}"
        )
    if name == "nary":
        # OMML 的积分、求和等运算符通过 m:chr 指定，缺省按积分处理。
        operator = _OPERATORS.get(attr(child(node, "m", "chr"), "m", "val") or "", r"\int")
        sub = _named(node, "sub")
        sup = _named(node, "sup")
        limits = f"_{{{sub}}}" if sub else ""
        limits += f"^{{{sup}}}" if sup else ""
        return f"{operator}{limits} {_named(node, 'e')}"
    if name == "func":
        function = _named(node, "fName").strip()
        value = _named(node, "e")
        return rf"\{function}{{{value}}}" if function else value
    if name == "d":
        return rf"\left( {_named(node, 'e')} \right)"
    if name == "m":
        # 矩阵行列使用 LaTeX 的 & 和换行分隔，保留 Word 中的二维结构。
        rows = []
        for row in children(node, "m", "mr"):
            rows.append(" & ".join(_convert(item) for item in children(row, "m", "e")))
        return r"\begin{matrix} " + r" \\ ".join(rows) + r" \end{matrix}"
    if name.endswith("Pr") or name in {"ctrlPr", "rPr"}:
        return ""
    return "".join(_convert(item) for item in node)


def _named(node: ET.Element, name: str) -> str:
    target = child(node, "m", name)
    return _convert(target) if target is not None else ""


def _group(value: str) -> str:
    # 单字符可直接加上下标，多字符或命令必须整体加花括号。
    return value if len(value) == 1 and "\\" not in value else f"{{{value}}}"
