def extract_markdown_title(markdown: str) -> str:
    """提取当前流程。"""
    for line in markdown.splitlines()[:20]:
        text = line.strip()
        if text.startswith("# "):
            return text.removeprefix("# ").strip()

    return ""
