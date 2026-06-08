import logging
import re
from html import unescape
from typing import Optional

import trafilatura

from common.logger import log_fail

# 规则与扫描特征常量
_HTML_DETECTION_SCAN_CHARS = 1024
_HTML_FRAGMENT_TAG_RE = re.compile(
    r"</?(html|head|body|main|article|section|div|p|h[1-6]|ul|ol|li|table|tr|td|a)\b",
    re.IGNORECASE,
)
_INLINE_TAG_RE = re.compile(r"<[^>]+>")

# 静默第三方库高频输出的无关日志
for _LOGGER_NAME in ("courlan", "htmldate", "trafilatura"):
    logging.getLogger(_LOGGER_NAME).setLevel(logging.ERROR)


def looks_like_html(content: str) -> bool:
    """通过扫描文本头部片段，快速判定内容是否为 HTML 完整文档或 HTML 代码片段。"""
    head = content[:_HTML_DETECTION_SCAN_CHARS]

    # 匹配文档类型声明或任意主流结构化 HTML 标签。
    return "<!doctype html" in head.lower() or bool(_HTML_FRAGMENT_TAG_RE.search(head))


def clean_inline_html_text(value: str) -> str:
    """清洗小段内联文本（如标题、网页片段、锚文本），剔除标签并还原 HTML 实体。"""
    stripped = _INLINE_TAG_RE.sub(" ", value)
    unescaped = unescape(stripped)

    # 压缩连续的换行和空白字符
    return re.sub(r"\s+", " ", unescaped).strip()


def html_unescape_url(value: str) -> str:
    """还原 HTML 属性中被转义的 URL 实体（例如将 &amp; 还原为 &）。"""
    return unescape(value).strip()


class HtmlToMarkdownExtractor:
    """基于 trafilatura 的网页核心正文抽取器，负责将 HTML 转换为精简的 Markdown。"""

    def extract(self, html: str) -> Optional[str]:
        """执行正文抽取，保留表格与链接，并在解析异常时兜底返回 None。"""
        stripped = html.strip()
        if not stripped:
            return None

        try:
            extracted = trafilatura.extract(
                stripped,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_links=True,
                favor_precision=False,
                favor_recall=True,
            )
        except Exception as e:
            log_fail("trafilatura 提取", repr(e))
            return None

        if not extracted:
            return None

        return extracted.strip()
