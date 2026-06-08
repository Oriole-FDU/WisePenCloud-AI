import re


def normalize_display_text(text: str) -> str:
    """规范化文本的显示排版与换行格式。

    本函数用于净化文本排版，执行以下标准清洗流程：
    1. 统一跨平台的换行符（将 \r\n 和 \r 统一替换为 \n）。
    2. 移除文本中每一行末尾的空白字符（空格、制表符等），但保留行首缩进。
    3. 裁剪掉整个文本首尾的多余空白与换行。
    4. 使用正则表达式将连续 3 个或以上的换行符（即大段连续空行）压缩为双换行（最多保留一个空行隔开段落）。

    Args:
        text: 需要进行排版净化的原始输入字符串。

    Returns:
        str: 规整排版后的干净文本字符串。
    """
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    result = "\n".join(lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result
