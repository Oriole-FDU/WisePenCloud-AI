from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pprint import pformat

from common.utils.chunkers import (
    ChunkDocument,
    MarkdownChunker,
    MarkdownChunkerConfig,
    MarkdownChunkingStrategy,
)


_TEST_DIR = Path(__file__).resolve().parent
_SOURCE_PATH = _TEST_DIR / "by_title_test.md"
_MARKED_SOURCE_PATH = _TEST_DIR / "by_page_test.md"
_OUTPUT_PATH = _TEST_DIR / "chunking_comparison.txt"


@dataclass(frozen=True, slots=True)
class ComparisonCase:
    name: str
    text: str
    strategy: MarkdownChunkingStrategy


# 这里只是为了演示分页行为，不代表原文真实页码。
_PAGE_BREAKS = (
    ("## 1. Introduction", "<!-- page 2 -->"),
    ("## 3. SkillX Design and Implementation", "<!-- page 3 -->"),
    ("## 5. Experiment", "<!-- page 4 -->"),
    ("## 7. Related Work", "<!-- page 5 -->"),
    ("## A. Detailed Experiments Settings", "<!-- page 6 -->"),
    ("## C.5. Merge Prompt", "<!-- page 7 -->"),
)


def main() -> None:
    source_text = _SOURCE_PATH.read_text(encoding="utf-8")
    marked_text = _add_page_markers(source_text)
    _MARKED_SOURCE_PATH.write_text(marked_text, encoding="utf-8")

    cases = (
        ComparisonCase(
            name="原始文件 + BY_TITLE",
            text=source_text,
            strategy=MarkdownChunkingStrategy.BY_TITLE,
        ),
        ComparisonCase(
            name="人工分页文件 + BY_TITLE",
            text=marked_text,
            strategy=MarkdownChunkingStrategy.BY_TITLE,
        ),
        ComparisonCase(
            name="人工分页文件 + BY_PAGE",
            text=marked_text,
            strategy=MarkdownChunkingStrategy.BY_PAGE,
        ),
        ComparisonCase(
            name="人工分页文件 + AUTO",
            text=marked_text,
            strategy=MarkdownChunkingStrategy.AUTO,
        ),
    )

    _OUTPUT_PATH.write_text(_render_cases(cases), encoding="utf-8")
    print(f"已生成带页标文件：{_MARKED_SOURCE_PATH}")
    print(f"已生成对比结果：{_OUTPUT_PATH}")


def _add_page_markers(text: str) -> str:
    marked_text = text
    for heading, marker in _PAGE_BREAKS:
        needle = f"\n{heading}"
        replacement = f"\n\n{marker}\n\n{heading}"
        if marked_text.count(needle) != 1:
            raise ValueError(f"分页标题不存在或不唯一：{heading}")
        marked_text = marked_text.replace(needle, replacement, 1)
    return f"<!-- page 1 -->\n\n{marked_text}"


def _render_cases(cases: tuple[ComparisonCase, ...]) -> str:
    sections: list[str] = [
        "Markdown 分块原始正文对比",
        "=" * 80,
        "",
        "说明：人工页标只用于演示，不代表原文真实分页。",
        "每个 CHUNK BEGIN 与 CHUNK END 之间都是 Chunk 对象的原生 repr，字段名和值均保持原样。",
        "BY_TITLE 使用标题边界，并允许跨 page marker；BY_PAGE 把 page marker 作为硬边界；",
        "AUTO 在检测到 page marker 后自动选择 BY_PAGE。",
        "",
    ]

    for case in cases:
        result = MarkdownChunker(
            MarkdownChunkerConfig(
                strategy=case.strategy,
                max_characters=12000,
                new_after_n_chars=6000,
            )
        ).chunk(document=ChunkDocument(text=case.text))
        sections.extend(
            (
                "CASE BEGIN: " + case.name,
                "",
            )
        )

        for chunk in result.chunks:
            sections.extend(
                (
                    f"CHUNK {chunk.chunk_index} BEGIN",
                    pformat(chunk, width=120, sort_dicts=False),
                    f"CHUNK {chunk.chunk_index} END",
                    "",
                )
            )

        sections.extend(("CASE END: " + case.name, "", "=" * 80, ""))

    return "\n".join(sections)


if __name__ == "__main__":
    main()
