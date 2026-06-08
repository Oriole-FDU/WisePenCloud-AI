import hashlib
import re
from dataclasses import dataclass
from typing import List, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from chat.application.rag.runtime.models import (
    ChunkingResult,
    RetrieveChunk,
    SearchChunk,
)
from chat.application.rag.runtime.models import RagResource


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """RAG 分块配置。

    - retrieve_chunk 是阅读单位，粒度较大。
    - search_chunk 是检索单位，粒度较小。
    - search_chunk 只能在单个 retrieve_chunk 内继续切分，不能跨父块。
    """

    retrieve_chunk_size: int = 2400
    retrieve_chunk_overlap: int = 200
    search_chunk_size: int = 600
    search_chunk_overlap: int = 100
    min_retrieve_chunk_size: int = 320
    min_search_chunk_size: int = 120


class RagChunker:
    """RAG 分块器。

    将资源原文按父子块两级分块：
    - retrieve_chunk（父块）：较大的阅读单位，用于最终的证据引用。
    - search_chunk（子块）：较小的检索单位，在单个父块内继续切分。
    """

    def __init__(self, config: ChunkingConfig) -> None:
        """初始化分块器。

        Args:
            config: 分块配置（父块/子块大小、重叠窗口）。
        """
        self._config = config
        self._retrieve_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.retrieve_chunk_size,
            chunk_overlap=config.retrieve_chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )
        self._search_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.search_chunk_size,
            chunk_overlap=config.search_chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )

    def build_chunks(self, resource: RagResource) -> ChunkingResult:
        """构建资源的父子二级分块。

        分块算法：
        1. 先将资源原文使用 retrieve_splitter 切成 retrieve_chunk（父块），
           以 (resource_kind:resource_id:retrieve:index:hash) 为 chunk_id。
        2. 再在每个 retrieve_chunk 内部使用 search_splitter 继续切分为 search_chunk（子块），
           以 (resource_kind:resource_id:search:parent_index:index:hash) 为 chunk_id。
        3. 子块不跨父块，通过 parent_chunk_id 关联。

        Args:
            resource: 待分块的资源。

        Returns:
            包含父块和子块列表的分块结果。
        """

        # 先将资源原文切成 retrieve_chunk，作为最终阅读和引用的父块。
        sectioned_text = _inject_heading_paths(resource.content)
        raw_retrieve_texts = self._retrieve_splitter.split_text(sectioned_text)
        retrieve_texts = _merge_short_tail_chunks(
            [text.strip() for text in raw_retrieve_texts if text.strip()],
            self._config.min_retrieve_chunk_size,
        )

        retrieve_chunks: List[RetrieveChunk] = []
        for chunk_index, text in enumerate(retrieve_texts):
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = (
                f"{resource.resource_kind.value}:"
                f"{resource.resource_id}:"
                f"retrieve:{chunk_index}:"
                f"{content_hash[:16]}"
            )

            retrieve_chunks.append(
                RetrieveChunk(
                    chunk_id=chunk_id,
                    resource_id=resource.resource_id,
                    resource_kind=resource.resource_kind,
                    chunk_index=chunk_index,
                    text=text,
                    content_hash=content_hash,
                )
            )

        # 再在每个 retrieve_chunk 内部切 search_chunk，确保子块不跨父块。
        search_chunks: List[SearchChunk] = []
        for parent_chunk in retrieve_chunks:
            raw_search_texts = self._search_splitter.split_text(parent_chunk.text)
            search_texts = _merge_short_tail_chunks(
                _merge_heading_only_chunks(
                    [text.strip() for text in raw_search_texts if text.strip()]
                ),
                self._config.min_search_chunk_size,
            )

            for chunk_index, text in enumerate(search_texts):
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                chunk_id = (
                    f"{resource.resource_kind.value}:"
                    f"{resource.resource_id}:"
                    f"search:{parent_chunk.chunk_index}:{chunk_index}:"
                    f"{content_hash[:16]}"
                )

                search_chunks.append(
                    SearchChunk(
                        chunk_id=chunk_id,
                        parent_chunk_id=parent_chunk.chunk_id,
                        resource_id=resource.resource_id,
                        resource_kind=resource.resource_kind,
                        parent_chunk_index=parent_chunk.chunk_index,
                        chunk_index=chunk_index,
                        text=text,
                        content_hash=content_hash,
                    )
                )

        return ChunkingResult(
            retrieve_chunks=retrieve_chunks,
            search_chunks=search_chunks,
        )


def _inject_heading_paths(text: str) -> str:
    """为 Markdown 标题下的正文补入轻量标题路径。"""
    blocks = _split_heading_sections(text)
    return "\n\n".join(
        _format_section(path=path, body=body)
        for path, body in blocks
        if body.strip()
    )


def _split_heading_sections(text: str) -> List[Tuple[List[str], str]]:
    heading_stack: List[str] = []
    current_lines: List[str] = []
    current_path: List[str] = []
    sections: List[Tuple[List[str], str]] = []
    in_fenced_code = False
    table_buffer: List[str] = []

    def flush_table() -> None:
        nonlocal current_lines, table_buffer
        if table_buffer:
            current_lines.append("\n".join(table_buffer))
            table_buffer = []

    for line in text.splitlines():
        if line.strip().startswith("```"):
            flush_table()
            in_fenced_code = not in_fenced_code
            current_lines.append(line)
            continue

        if in_fenced_code:
            current_lines.append(line)
            continue

        if _is_markdown_table_line(line):
            table_buffer.append(line)
            continue

        flush_table()
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is None:
            current_lines.append(line)
            continue

        if current_lines:
            sections.append((current_path, "\n".join(current_lines).strip()))
            current_lines = []

        level = len(match.group(1))
        title = match.group(2).strip()
        heading_stack = heading_stack[:level - 1]
        heading_stack.append(title)
        current_path = list(heading_stack)

    flush_table()
    if current_lines:
        sections.append((current_path, "\n".join(current_lines).strip()))

    return sections if sections else [([], text.strip())]


def _format_section(*, path: List[str], body: str) -> str:
    stripped_body = body.strip()
    if not path:
        return stripped_body

    heading_path = " > ".join(path)
    if stripped_body.startswith(f"Section: {heading_path}\n"):
        return stripped_body

    return f"Section: {heading_path}\n{stripped_body}"


def _merge_short_tail_chunks(chunks: List[str], min_size: int) -> List[str]:
    """把过短尾块并入前一个块，减少低信息密度碎片。"""
    if len(chunks) <= 1:
        return chunks

    merged: List[str] = []
    for chunk in chunks:
        if merged and len(chunk) < min_size:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)

    return merged


def _merge_heading_only_chunks(chunks: List[str]) -> List[str]:
    """把只有标题路径的子块并入相邻正文，避免低价值检索块。"""
    merged: List[str] = []
    pending_heading: str = ""

    for chunk in chunks:
        if _is_heading_only_chunk(chunk):
            if pending_heading:
                pending_heading = f"{pending_heading}\n{chunk}"
            else:
                pending_heading = chunk
            continue

        if pending_heading:
            merged.append(f"{pending_heading}\n{chunk}")
            pending_heading = ""
        else:
            merged.append(chunk)

    if pending_heading:
        if merged:
            merged[-1] = f"{merged[-1]}\n{pending_heading}"
        else:
            merged.append(pending_heading)

    return merged


def _is_heading_only_chunk(chunk: str) -> bool:
    return all(
        line.startswith("Section: ")
        for line in chunk.splitlines()
        if line.strip()
    )


def _is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    if stripped.startswith("|") and stripped.endswith("|"):
        return True
    return bool(re.match(r"^:?-{3,}:?(\s*\|\s*:?-{3,}:?)+$", stripped))
