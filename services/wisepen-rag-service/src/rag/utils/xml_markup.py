"""构造 XML 片段时使用的安全转义工具。"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape


def xml_attr(value: Any) -> str:
    return escape(str(value), {'"': "&quot;"})


def xml_cdata(text: str) -> str:
    return f"<![CDATA[{text.replace(']]>', ']]]]><![CDATA[>')}]]>"
