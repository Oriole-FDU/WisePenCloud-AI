import re

_EXCESSIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """归一化文本：统一换行符、去除行尾空格、压缩多余空行。"""
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    result = "\n".join(lines).strip()
    return _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", result)
