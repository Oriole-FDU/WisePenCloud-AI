import logging
import re
from typing import Optional

import trafilatura
from playwright.async_api import Page

from common.logger import log_fail

_MIN_CONTENT_LENGTH = 120
_HTML_DETECTION_SCAN_CHARS = 1024
_HTML_FRAGMENT_TAG_RE = re.compile(
    r"</?(html|head|body|main|article|section|div|p|h[1-6]|ul|ol|li|table|tr|td|a)\b",
    re.IGNORECASE,
)

# 第三方抽取库在失败页面上会产生大量无关日志，统一降噪到错误级别。
for _LOGGER_NAME in ("courlan", "htmldate", "trafilatura"):
    logging.getLogger(_LOGGER_NAME).setLevel(logging.ERROR)

_RENDERED_CONTENT_SCRIPT = r"""() => {
    const candidateSelectors = [
        '#mw-content-text',
        '.mw-parser-output',
        'article',
        'main',
        '[role="main"]',
        '#content',
        '#main',
        '.content',
        '.main',
        '.article',
        '.markdown-body',
        '.repository-content',
        '.wiki-content',
        '.post',
        '.entry-content',
        '.document',
        '.docs-content'
    ];

    const removeSelectors = [
        'script',
        'style',
        'noscript',
        'template',
        'svg',
        'canvas',
        'iframe',
        '[hidden]',
        '[aria-hidden="true"]',
        '.mw-editsection',
        '.reference',
        '.reflist',
        '.navbox',
        '.metadata',
        '.ambox',
        '.vertical-navbox',
        '.sistersitebox',
        '.printfooter',
        '.catlinks',
        '#toc',
        '.toc'
    ];

    function cleanText(text) {
        return (text || '')
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
            .split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0)
            .join('\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function cloneAndClean(element) {
        const clone = element.cloneNode(true);

        for (const selector of removeSelectors) {
            clone.querySelectorAll(selector).forEach(node => node.remove());
        }

        return clone;
    }

    function extractText(element) {
        if (!element) {
            return '';
        }

        const cleaned = cloneAndClean(element);
        return cleanText(cleaned.innerText || cleaned.textContent || '');
    }

    function scoreText(text, selectorIndex) {
        if (!text) {
            return -1;
        }

        let score = Math.min(text.length, 20000);
        score += Math.max(0, 1000 - selectorIndex * 80);

        const lineCount = text.split('\n').filter(Boolean).length;
        score += Math.min(lineCount * 8, 800);

        return score;
    }

    let bestText = '';
    let bestScore = -1;

    for (let i = 0; i < candidateSelectors.length; i += 1) {
        const selector = candidateSelectors[i];
        const candidates = Array.from(document.querySelectorAll(selector));

        for (const element of candidates) {
            const text = extractText(element);
            const score = scoreText(text, i);

            if (score > bestScore) {
                bestText = text;
                bestScore = score;
            }
        }
    }

    if (bestText) {
        return bestText;
    }

    if (document.body) {
        const bodyClone = cloneAndClean(document.body);
        return cleanText(bodyClone.innerText || bodyClone.textContent || '');
    }

    return '';
}"""


class BrowserContentExtractor:
    """浏览器页面正文抽取器。

    优先读取渲染后的可见正文；当页面可见文本不足时，回退到 HTML 到 Markdown
    转换。HTML 转换完全依赖 trafilatura，避免引入规则驱动的正文兜底。
    """

    def __init__(self, min_content_length: int = _MIN_CONTENT_LENGTH) -> None:
        """初始化正文抽取器。

        Args:
            min_content_length: 判定为有效正文的最小字符数。
        """
        self._min_content_length = min_content_length

    async def extract(self, page: Page) -> str:
        """从 Playwright Page 中抽取适合返回给 LLM 的 Markdown/纯文本。

        Args:
            page: 当前浏览器页面。

        Returns:
            str: 清洗后的正文，失败或受限页面返回空字符串。
        """
        rendered = await self._extract_rendered_content(page)
        if self._is_usable_content(rendered):
            return rendered

        html = await page.content()
        converted = self.process_raw_content(html)
        if converted:
            return converted

        return ""

    def process_raw_content(self, content: str) -> Optional[str]:
        """处理原始 HTML 或文本内容。

        Args:
            content: page.content() 或其它原始网页内容。

        Returns:
            Optional[str]: 清洗后的 Markdown/文本；无有效正文时返回 None。
        """
        stripped = content.strip()
        if not stripped:
            return None

        markdown = stripped
        if looks_like_html(stripped):
            markdown = extract_html_to_markdown(stripped)
            if not markdown:
                return None

        normalized = normalize_display_text(markdown)
        if not self._is_usable_content(normalized):
            return None

        return normalized

    async def _extract_rendered_content(self, page: Page) -> str:
        content = await page.evaluate(_RENDERED_CONTENT_SCRIPT)
        if not isinstance(content, str):
            return ""
        return normalize_display_text(content)

    def _is_usable_content(self, text: str) -> bool:
        if len(text.strip()) < self._min_content_length:
            return False
        return bool(re.search(r"\w", text))


def normalize_display_text(text: str) -> str:
    """规范化页面正文换行和段落空白。

    Args:
        text: 原始页面正文。

    Returns:
        str: 去除尾部空白并压缩超长空行后的文本。
    """
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip())


def looks_like_html(content: str) -> bool:
    """快速判断内容是否为完整 HTML 文档或 HTML 片段。

    Args:
        content: 原始页面内容。

    Returns:
        bool: 文本头部包含 HTML 文档或结构化标签时为 True。
    """
    head = content[:_HTML_DETECTION_SCAN_CHARS]
    return "<!doctype html" in head.lower() or bool(_HTML_FRAGMENT_TAG_RE.search(head))


def extract_html_to_markdown(html: str) -> Optional[str]:
    """使用 trafilatura 将 HTML 转为 Markdown，空结果保持为空。

    Args:
        html: 待抽取的 HTML 文档或片段。

    Returns:
        Optional[str]: trafilatura 抽取出的 Markdown；解析失败或空结果返回 None。
    """
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
    except Exception as error:
        log_fail("browser HTML 转 Markdown", repr(error))
        return None

    if not extracted:
        return None

    return extracted.strip()
