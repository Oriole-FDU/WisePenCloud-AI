"""沿用本地 HTML 清洗脚本：移除通用 DOM 噪声，只保留适合索引的文本。"""

from __future__ import annotations

import re
import unicodedata

from selectolax.lexbor import LexborHTMLParser

# ============================================================================
# LaTeXML 通用 DOM 噪声选择器（全部基于 LaTeXML 标准 class，不依赖具体论文措辞）
# ============================================================================
NOISE_SELECTORS = ",".join(  # noqa: FLY002 - 保留分组选择器，方便维护清洗规则。
    [
        # 1. 页面外壳、导航、目录与多媒体元素
        "head",
        "script",
        "style",
        "link",
        "meta",
        "noscript",
        "template",
        "iframe",
        "dialog",
        "header",
        "footer",
        "nav",
        "aside",
        "form",
        "button",
        "input",
        "textarea",
        "select",
        "img",
        "svg",
        "canvas",
        "video",
        "audio",
        "picture",
        ".arxiv-html-header",
        ".arxiv-html-footer",
        ".ds-site-footer",
        ".ds-announcement",
        "#infobox",
        ".infobox",
        ".ltx_page_header",
        ".ltx_page_footer",
        ".ltx_page_navbar",
        ".ltx_page_logo",
        ".ltx_TOC",
        ".ltx_toclist",
        ".ltx_tocentry",
        # 2. 检索噪音：参考文献整节（最核心）
        "section.ltx_bibliography",
        ".ltx_bibliography",
        ".ltx_biblist",
        ".ltx_bibitem",
        ".ltx_bibblock",
        # 3. 检索噪音：数学公式与公式排版表格
        "math",
        ".ltx_Math",
        ".ltx_math_unparsed",
        ".ltx_equation",
        ".ltx_eqn_table",
        ".ltx_equationgroup",
        ".ltx_eqn_row",
        ".ltx_eqn_cell",
        ".ltx_eqn_eqno",
        # 4. 检索噪音：正文中的引用跳转标记、编号标签与注脚（含内联注脚正文）
        "cite",
        "cite.ltx_cite",
        ".ltx_cite",
        ".ltx_note_mark",
        ".ltx_note",
        ".ltx_note_outer",
        ".ltx_note_content",
        ".ltx_note_type",
        ".ltx_role_footnote",
        ".ltx_role_footnotemark",
        ".ltx_role_thanks",
        ".ltx_role_endnote",
        ".ltx_tag",
        ".ltx_role_refnum",
        "a.ltx_url",
        "a[href^='mailto:']",
        # 5. 检索噪音：作者机构邮箱、编译日期、分类号、致谢与模板小标题
        ".ltx_authors",
        ".ltx_author_notes",
        ".ltx_creator",
        ".ltx_personname",
        ".ltx_contact",
        ".ltx_role_author",
        ".ltx_role_affiliation",
        ".ltx_role_email",
        ".ltx_role_address",
        ".ltx_dates",
        ".ltx_classification",
        ".ltx_acknowledgement",
        ".ltx_acknowledgements",
        ".ltx_title_abstract",
        ".ltx_title_keywords",
        ".ltx_title_classification",
        ".ltx_title_bibliography",
        # 6. 检索噪音：图表、数据表格、代码/Prompt 框与排版报错块
        "figure",
        ".ltx_figure",
        ".ltx_table",
        ".ltx_float",
        "figcaption",
        ".ltx_caption",
        ".ltx_figure_panel",
        ".ltx_flex_figure",
        "table",
        ".ltx_tabular",
        ".ltx_listing",
        ".ltx_Listing",
        ".ltx_lstlisting",
        ".ltx_verbatim",
        "pre",
        "code",
        ".ltx_rule",
        ".ltx_pagination",
        ".ltx_ERROR",
    ]
)

# 仅做最基础的字符与空括号清理，不做激进的文本正则替换
INVISIBLE_CHARS_RE = re.compile(r"[\u00ad\u200b\u200c\u200d\u2060-\u2064\ufeff]")
EMPTY_BRACKETS_RE = re.compile(r"\(\s*[,;:.…\-–—]*\s*\)|\[\s*[,;:.…\-–—]*\s*\]")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:?!])")
WS_RE = re.compile(r"[ \t\f\v]+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = INVISIBLE_CHARS_RE.sub("", text)
    text = EMPTY_BRACKETS_RE.sub(" ", text)
    text = SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return WS_RE.sub(" ", text).strip()


def clean_arxiv_html(html: str, *, min_body_chars: int = 500) -> dict[str, str] | None:
    tree = LexborHTMLParser(html)
    article = (
        tree.css_first("article.ltx_document")
        or tree.css_first(".ltx_page_content")
        or tree.css_first(".ltx_page_main")
        or tree.body
    )
    if article is None:
        return None

    # 1. 剥离所有通用噪声 DOM 节点
    for node in article.css(NOISE_SELECTORS):
        node.decompose()

    # 2. 提取标题与摘要，并从正文中移除避免重复索引
    title_node = article.css_first("h1.ltx_title_document")
    title = (
        normalize_text(title_node.text(separator=" ", strip=True)) if title_node else ""
    )
    if title_node is not None:
        title_node.decompose()

    abs_node = article.css_first(".ltx_abstract")
    abstract = (
        normalize_text(abs_node.text(separator=" ", strip=True)) if abs_node else ""
    )
    if abs_node is not None:
        abs_node.decompose()

    # 3. 提取正文文本
    blocks: list[str] = []
    for node in article.css("h2, h3, h4, h5, h6, .ltx_para"):
        txt = normalize_text(node.text(separator=" ", strip=True))
        if txt:
            blocks.append(txt)

    body = (
        "\n\n".join(blocks)
        if blocks
        else normalize_text(article.text(separator=" ", strip=True))
    )
    if len(body) < min_body_chars:
        return None

    return {
        "title": title,
        "abstract": abstract,
        "body": body,
        "text": "\n\n".join(part for part in (title, abstract, body) if part),
    }
