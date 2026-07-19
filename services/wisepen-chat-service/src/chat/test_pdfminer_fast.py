from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams

_DEFAULT_PDF = Path(r"C:\Users\12732\Downloads\2607.05577v1.pdf")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用 pdfminer.six 快速提取 PDF 文本并输出 Markdown 与质量报告。"
    )
    parser.add_argument("pdf", nargs="?", type=Path, default=_DEFAULT_PDF)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；默认写入 PDF 所在目录。",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=1200,
        help="终端预览每页最多显示的字符数，默认 1200。",
    )
    parser.add_argument(
        "--preview-pages",
        type=int,
        default=3,
        help="终端预览前几页，默认 3。",
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf.expanduser().resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF 不存在：{pdf_path}")

    output_dir = (args.output_dir or pdf_path.parent).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    raw_text = extract_text(pdf_path, laparams=LAParams())
    elapsed_seconds = time.perf_counter() - started

    raw_pages = raw_text.split("\f")
    while raw_pages and not raw_pages[-1].strip():
        raw_pages.pop()

    pages: list[str] = []
    page_reports: list[dict[str, int | float | bool]] = []

    for page_number, raw_page in enumerate(raw_pages, start=1):
        text = raw_page.replace("\r\n", "\n").replace("\r", "\n")
        text = _TRAILING_SPACE_RE.sub("\n", text)
        text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text).strip()

        non_whitespace_chars = sum(not char.isspace() for char in text)
        replacement_chars = text.count("\ufffd")
        lines = [line for line in text.splitlines() if line.strip()]
        short_line_ratio = (
            sum(len(line.strip()) <= 2 for line in lines) / len(lines)
            if lines
            else 0.0
        )

        page_reports.append(
            {
                "page": page_number,
                "chars": len(text),
                "non_whitespace_chars": non_whitespace_chars,
                "lines": len(lines),
                "short_line_ratio": round(short_line_ratio, 4),
                "replacement_chars": replacement_chars,
                "empty": non_whitespace_chars == 0,
            }
        )

    markdown = "\n\n".join(pages).rstrip() + "\n"
    total_chars = sum(int(page["chars"]) for page in page_reports)
    non_whitespace_chars = sum(
        int(page["non_whitespace_chars"]) for page in page_reports
    )
    empty_pages = [
        int(page["page"]) for page in page_reports if bool(page["empty"])
    ]
    low_text_pages = [
        int(page["page"])
        for page in page_reports
        if int(page["non_whitespace_chars"]) < 32
    ]
    replacement_chars = sum(
        int(page["replacement_chars"]) for page in page_reports
    )

    report = {
        "source": str(pdf_path),
        "parser": "pdfminer.six",
        "laparams": {
            "line_overlap": 0.5,
            "char_margin": 2.0,
            "line_margin": 0.5,
            "word_margin": 0.1,
            "boxes_flow": 0.5,
            "detect_vertical": False,
            "all_texts": False,
        },
        "file_size_bytes": pdf_path.stat().st_size,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "page_count": len(raw_pages),
        "total_chars": total_chars,
        "non_whitespace_chars": non_whitespace_chars,
        "chars_per_second": (
            round(non_whitespace_chars / elapsed_seconds, 2)
            if elapsed_seconds
            else None
        ),
        "empty_pages": empty_pages,
        "low_text_pages": low_text_pages,
        "replacement_chars": replacement_chars,
        "replacement_ratio": (
            round(replacement_chars / max(total_chars, 1), 8)
        ),
        "pages": page_reports,
    }

    markdown_path = output_dir / f"{pdf_path.stem}.pdfminer.md"
    report_path = output_dir / f"{pdf_path.stem}.pdfminer.report.json"
    markdown_path.write_text(markdown, encoding="utf-8")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 80)
    print(f"PDF:              {pdf_path}")
    print(f"文件大小:         {pdf_path.stat().st_size / 1024 / 1024:.2f} MiB")
    print(f"页数:             {len(raw_pages)}")
    print(f"耗时:             {elapsed_seconds:.3f} s")
    print(f"非空白字符:       {non_whitespace_chars:,}")
    print(f"吞吐:             {report['chars_per_second']:,} chars/s")
    print(f"空白页:           {empty_pages or '无'}")
    print(f"低文本页(<32字):  {low_text_pages or '无'}")
    print(f"替换字符 U+FFFD:  {replacement_chars}")
    print(f"Markdown:         {markdown_path}")
    print(f"报告:             {report_path}")
    print("=" * 80)

    preview_pages = min(max(args.preview_pages, 0), len(pages))
    preview_chars = max(args.preview_chars, 0)
    for index in range(preview_pages):
        page_text = pages[index]
        print(f"\n--- 第 {index + 1} 页预览 ---\n")
        print(page_text[:preview_chars])
        if len(page_text) > preview_chars:
            print("\n...[已截断]")


if __name__ == "__main__":
    main()