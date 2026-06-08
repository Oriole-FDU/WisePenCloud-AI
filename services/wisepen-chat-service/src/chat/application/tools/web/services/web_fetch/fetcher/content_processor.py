from typing import Optional

from chat.application.tools.web.services.web_fetch.utils.display_text import (
    normalize_display_text,
)
from chat.application.tools.web.services.web_fetch.utils.page_block import (
    looks_like_blocked_page,
)
from chat.application.tools.web.utils.html import (
    HtmlToMarkdownExtractor,
    looks_like_html,
)


class ContentProcessor:
    """将 web_fetch 获取到的原始 HTML/文本内容转换为纯净的 Markdown 格式。"""

    def __init__(self, min_content_length: int = 400):
        """初始化 HTML 提取器和内容长度阈值。"""
        self._min_content_length = min_content_length
        self._html_extractor = HtmlToMarkdownExtractor()

    def process(self, content: str) -> Optional[str]:
        """对输入内容执行 HTML 转 Markdown、排版规范化及反爬页面检测。

        处理流程：HTML 清洗 -> 显示排版归一化 -> 长度门槛校验 -> 反爬页面检测。
        """
        stripped = content.strip()
        if not stripped:
            return None

        markdown = stripped

        # 检测并转换 HTML 内容为 Markdown
        if looks_like_html(stripped):
            extracted = self._html_extractor.extract(stripped)
            if not extracted:
                return None
            markdown = extracted

        # 排版归一化后校验内容长度是否达到最低门槛
        normalized_text = normalize_display_text(markdown)
        if len(normalized_text) < self._min_content_length:
            return None

        # 过滤反爬/验证码/错误页面
        if looks_like_blocked_page(normalized_text):
            return None

        return normalized_text