from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .models import BlockKind, DocumentBlock

# 顺序从语义较强的段落/换行逐级降到字符
_SEPARATORS = (
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


def split_plain_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[DocumentBlock, ...]:
    """按纯文本边界递归切分，并恢复每段在输入文本中的字符位置。

    该函数只接收已经确认的 oversized block，因此 overlap 只发生在该 block
    的递归子块之间，不会跨完整 Markdown block 传播。
    """
    if not text:
        return ()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=list(_SEPARATORS),
    )
    raw_chunks = splitter.split_text(text)
    blocks: list[DocumentBlock] = []
    cursor = 0
    for index, chunk_text in enumerate(raw_chunks):
        # splitter 返回的是文本而不是源坐标
        # 从上一个重叠位置开始精确匹配，获取 chunk 在原文中的 Python 字符偏移范围
        search_from = max(0, cursor - chunk_overlap)
        start = text.find(chunk_text, search_from)
        if start < 0:
            raise ValueError("recursive splitter returned text outside its source")
        end = start + len(chunk_text)
        blocks.append(
            DocumentBlock(
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
