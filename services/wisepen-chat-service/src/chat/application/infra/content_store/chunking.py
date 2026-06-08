from typing import List, Optional

from langchain_text_splitters.character import RecursiveCharacterTextSplitter

from .models import ContentChunk

# 预定义的分块边界切分符
_PLAIN_TEXT_SEPARATORS = ["\n\n", "\n", " ", ""]
_MARKDOWN_SEPARATORS = [
    "\n\n# ",
    "\n\n## ",
    "\n\n### ",
    "\n\n#### ",
    "\n\n```",
    "\n\n---",
    "\n\n",
    "\n",
    "。",
    ". ",
    " ",
    "",
]

_MARKDOWN_CONTENT_TYPE = "text/markdown"


def _separators_for_content_type(content_type: Optional[str]) -> List[str]:
    """
    根据媒体格式自适应路由文本切分符优先级集。
    后续可能针对性拓展形式。
    """
    if content_type == _MARKDOWN_CONTENT_TYPE:
        return _MARKDOWN_SEPARATORS

    return _PLAIN_TEXT_SEPARATORS


def create_content_chunks(
        text: str, chunk_size: int, *, content_type: Optional[str] = None
) -> List[ContentChunk]:
    """
    通用基础设施层的内容物理切片机。
    利用递归字符切分算法，将长文本转化为首尾相连、完全无重叠、单调递增的连续物理切片索引。

    - text: 待切片的原始核心文本内容
    - chunk_size: 单个分块的目标最大字符容量限制
    - content_type: 文本的媒体格式类型，用于自适应匹配最佳切分符集
    """
    if not text:
        return []

    splitter = RecursiveCharacterTextSplitter(
        # 核心底线：chunk_overlap 必须固定为 0，确保滑动视窗流式读取时的物理无重叠与数据保真
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=_separators_for_content_type(content_type),
        length_function=len,
        add_start_index=True,
        strip_whitespace=False,  # 禁止剔除空白符，否则无法严格还原原始文本的物理绝对偏移量
    )

    documents = [
        document
        for document in splitter.create_documents([text])
        if document.page_content
    ]

    chunks: List[ContentChunk] = []
    previous_end = 0

    for index, document in enumerate(documents):
        piece = document.page_content
        start = document.metadata.get("start_index")

        # 防元数据丢失
        if not isinstance(start, int) or start < 0:
            raise ValueError(
                "Missing valid chunk start_index metadata: "
                f"chunk_index={index}, previous_end={previous_end}, "
                f"piece_length={len(piece)}, piece_preview={piece[:80]!r}"
            )

        end = start + len(piece)

        # 防字符范围越界
        if end > len(text):
            raise ValueError(
                f"Chunk end_offset exceeds text length: "
                f"chunk_index={index}, end_offset={end}, text_length={len(text)}"
            )

        # 物理完整性回刷校验，确保切片文本与原始文本完全同构
        if text[start:end] != piece:
            raise ValueError(
                "Chunk text does not match source text at start_index: "
                f"chunk_index={index}, start={start}, end={end}, "
                f"piece_preview={piece[:80]!r}"
            )

        # 防长度异常倒挂
        if end <= start:
            raise ValueError(
                f"Chunk end_offset must be greater than start_offset: "
                f"chunk_index={index}, start={start}, end={end}"
            )

        # 断言连续性，任何一个分块如果跟前序分块断层，立刻阻断
        if start != previous_end:
            raise ValueError(
                f"Chunk offsets must be contiguous and monotonic: "
                f"chunk_index={index}, start={start}, previous_end={previous_end}"
            )

        chunks.append(
            ContentChunk(
                index=index,
                start_offset=start,
                end_offset=end,
            )
        )

        previous_end = end

    return chunks



def find_chunk_by_offset(
        chunks: List[ContentChunk], offset: int
) -> Optional[ContentChunk]:
    """
    根据字符绝对偏移量，快速检索目标落点的 ContentChunk。

    - chunks: 递增排列的连续物理切片列表
    - offset: 检索的目标字符绝对偏移量
    """
    offset = max(0, offset)

    # 由于 Chunks 数组天生单调非重叠递增，首个 end_offset 超过目标点的 chunk 便是解
    for chunk in chunks:
        if chunk.end_offset > offset:
            return chunk

    return None