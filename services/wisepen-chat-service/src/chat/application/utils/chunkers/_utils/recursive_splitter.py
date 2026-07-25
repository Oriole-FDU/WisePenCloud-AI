from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..models import BlockKind, ChunkDocument, TextBlock

_PLAIN_TEXT_SEPARATORS = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ".",
    "!",
    "?",
    " ",
    "",
)
_MARKDOWN_SEPARATORS = (
    "\n## ",
    "\n### ",
    "\n#### ",
    "\n\n",
    "\n",
    "。",
    ".",
    " ",
    "",
)


def split_plain_text(
    document: ChunkDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[TextBlock, ...]:
    """按段落、换行、句子到字符的优先级递归切分纯文本。"""
    return _split_recursive_text(
        document,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_PLAIN_TEXT_SEPARATORS,
    )


def split_markdown_text(
    document: ChunkDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[TextBlock, ...]:
    """切分单个 oversized Markdown block，优先使用结构化分隔符。"""
    return _split_recursive_text(
        document,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_MARKDOWN_SEPARATORS,
    )


def _split_recursive_text(
    document: ChunkDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
    separators: tuple[str, ...],
) -> tuple[TextBlock, ...]:
    """适配第三方递归切分器，并恢复每段文本在原文中的准确位置。"""
    if not document.text:
        return ()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=list(separators),
    )
    raw_chunks = splitter.split_text(document.text)
    # 第三方 splitter 只返回文本，下面按出现顺序恢复每片的原文区间。
    blocks: list[TextBlock] = []
    cursor = 0

    for index, chunk_text in enumerate(raw_chunks):
        # overlap 会让下一个块从 cursor 之前开始，只在允许的重叠窗口内回找。
        search_from = max(0, cursor - chunk_overlap) if chunk_overlap > 0 else cursor
        start = document.text.find(chunk_text, search_from)
        if start < 0:
            start = cursor
        end = start + len(chunk_text)
        blocks.append(
            TextBlock(
                block_id=f"block-{index}",
                text=chunk_text,
                block_kind=BlockKind.PARAGRAPH,
                block_index=index,
                start_offset=start,
                end_offset=end,
            )
        )
        cursor = end

    return tuple(blocks)
