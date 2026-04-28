from readability import Document
from markdownify import markdownify

from common.logger import log_fail


def extract_main_content(html: str) -> str:
    """使用 readability 提取页面主体内容，去除导航、侧栏等噪音"""
    try:
        return Document(html).summary()
    except Exception as e:
        log_fail("readability 提取主体内容", e)
        return html


def convert_to_markdown(html: str) -> str:
    """将 HTML 转换为 Markdown 格式"""
    try:
        return markdownify(
            html,
            heading_style="ATX",
            strip=["script", "style", "img", "nav", "footer"],
            autolinks=True,
        ).strip()
    except Exception as e:
        log_fail("HTML 转 Markdown", e)
        return html
