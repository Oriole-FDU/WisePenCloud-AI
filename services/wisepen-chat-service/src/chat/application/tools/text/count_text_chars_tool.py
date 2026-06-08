import json
from enum import StrEnum
from typing import Any, Dict, Iterable

import regex
from markdown_it import MarkdownIt
from markdown_it.token import Token

from chat.domain.interfaces.tool import BaseTool

_TOOL_DESCRIPTION = (
    "Counts text length, word count, and character count using multiple common metrics. "
    "By default, this tool removes Markdown formatting before counting visible plain text. "
    "Call this tool when the user asks about how many characters, how many words, word count, "
    "Chinese字数, text length, or whether text is within an N-character or N-word limit. "
    "The tool returns all common count metrics at once; after calling, choose the field that "
    "matches the user's wording. For ordinary Chinese 字数, prefer natural_text_count. "
    "When the user explicitly asks for 字符数 or character count, use "
    "character_count_with_spaces or character_count_without_spaces according to whether spaces "
    "should count. When the user explicitly asks for English word count or word count, use "
    "word_count. This tool does not count tokens and does not use a tokenizer."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "description": "Text to count.",
        },
        "strip_markdown": {
            "type": "boolean",
            "default": True,
            "description": (
                "Whether to remove Markdown formatting before counting visible plain text. "
                "Defaults to true."
            ),
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}

_MARKDOWN = MarkdownIt("commonmark")

# CJK 文字（汉字、假名、谚文）
_CJK_RE = regex.compile(
    r"[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]"
)
_PUNCTUATION_RE = regex.compile(r"\p{P}")     # 标点
_WHITESPACE_RE = regex.compile(r"\p{White_Space}")  # 空白字符
_NUMBER_SEQUENCE_RE = regex.compile(r"\p{N}+")      # 连续数字序列（计为一个单元）
# 非 CJK 单词（Latin 等），使用 Unicode 属性边界
_NON_CJK_WORD_RE = regex.compile(
    r"(?V1)"
    r"(?<![\p{L}\p{N}_])"
    r"(?:[\p{L}&&[^\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]]"
    r"[\p{L}\p{M}'_-]*)"
    r"(?![\p{L}\p{N}_])"
)


class _TokenType(StrEnum):
    """markdown-it 解析结果中实际用到的 token 类型。"""
    CODE_INLINE = "code_inline"
    CODE_BLOCK  = "code_block"
    FENCE       = "fence"
    TEXT        = "text"
    SOFTBREAK   = "softbreak"
    HARDBREAK   = "hardbreak"
    IMAGE       = "image"
    INLINE      = "inline"


# 直接输出 .content 作为纯文本的 token 类型
_YIELD_CONTENT_TYPES: frozenset[str] = frozenset({
    _TokenType.CODE_INLINE,
    _TokenType.CODE_BLOCK,
    _TokenType.FENCE,
    _TokenType.TEXT,
    _TokenType.IMAGE,
})

# 映射为换行符的 token 类型
_LINEBREAK_TYPES: frozenset[str] = frozenset({
    _TokenType.SOFTBREAK,
    _TokenType.HARDBREAK,
})


class CountTextCharsTool(BaseTool):
    @property
    def name(self) -> str:
        return "count_text_chars"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("text",)

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        text = kwargs["text"]
        strip_markdown = kwargs.get("strip_markdown", True)

        if not isinstance(text, str):
            return "[Tool Error] count_text_chars failed: text must be a string."
        if not isinstance(strip_markdown, bool):
            return "[Tool Error] count_text_chars failed: strip_markdown must be a boolean."

        counted_text = strip_markdown_to_text(text) if strip_markdown else text
        result = count_text_chars(counted_text)
        result["strip_markdown"] = strip_markdown
        result["counted_text"] = counted_text

        return "\n".join([
            "[Tool Result] count_text_chars",
            "",
            json.dumps(result, ensure_ascii=False, indent=2),
            "",
            "Assistant instructions:",
            "- Use natural_text_count for ordinary Chinese 字数 questions.",
            "- Use word_count for explicit English word count / word count questions.",
            "- Use character_count_with_spaces or character_count_without_spaces for explicit character count questions.",
            "- Do not describe these counts as token counts.",
        ])


def count_text_chars(text: str) -> Dict[str, int]:
    """计算文本各维度的长度指标。"""
    cjk_count    = len(_CJK_RE.findall(text))
    word_count   = len(_NON_CJK_WORD_RE.findall(text))
    num_count    = len(_NUMBER_SEQUENCE_RE.findall(text))
    ws_count     = len(_WHITESPACE_RE.findall(text))
    char_total   = len(text)

    return {
        # CJK字 + 非CJK词 + 数字序列，对应日常"字数"概念
        "natural_text_count":             cjk_count + word_count + num_count,
        "cjk_character_count":            cjk_count,
        "word_count":                     word_count,
        "number_sequence_count":          num_count,
        "character_count_with_spaces":    char_total,
        "character_count_without_spaces": char_total - ws_count,
        "punctuation_count":              len(_PUNCTUATION_RE.findall(text)),
        "whitespace_count":               ws_count,
    }


def strip_markdown_to_text(text: str) -> str:
    """将 Markdown 解析为纯文本，段落间用单空格连接。"""
    parts = _plain_text_parts(_MARKDOWN.parse(text))
    return " ".join(s for p in parts if (s := p.strip()))


def _plain_text_parts(tokens: Iterable[Token]) -> Iterable[str]:
    """深度优先遍历 token 树，产出纯文本片段。"""
    for token in tokens:
        if token.type in _YIELD_CONTENT_TYPES:
            if token.content:
                yield token.content
        elif token.type in _LINEBREAK_TYPES:
            yield "\n"
        elif token.type == _TokenType.INLINE and token.children:
            yield from _plain_text_parts(token.children)
        # 其余类型（html_block / html_inline / link_open 等）隐式跳过
