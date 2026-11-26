"""将会话本地文档转换成 Markdown，标准输出只返回读取回执。"""

import argparse
import json
from pathlib import Path
import sys

import anydoc


SUPPORTED_FORMATS = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv"}
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        workspace = Path.cwd().resolve()
        source = args.source.resolve()
        output = args.output.resolve()
        source.relative_to(workspace)
        relative_output = output.relative_to(workspace)
        if output.suffix.lower() != ".md" or source == output:
            raise ValueError("output must be a distinct .md file")

        # 单次读取供格式识别和转换共用，扫描页处理由 PDF 指南按需接管。
        with source.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds the 100 MiB conversion limit")
        file_format = anydoc.format_from_bytes(data)
        if file_format is None and source.suffix.lower() == ".csv":
            file_format = "csv"
        if file_format not in SUPPORTED_FORMATS:
            raise ValueError("unsupported document format")
        markdown = anydoc.to_markdown_bytes(data, file_format)
        # AnyDoc 将 BIFF 工作簿映射到 xlsx 解析器，回执保留实际的旧版格式。
        if file_format == "xlsx" and data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
            file_format = "xls"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown)
    except anydoc.NeedsOcrError as exc:
        print(json.dumps({
            "status": "needs_ocr", "format": "pdf", "pages": exc.pages, "page_count": exc.page_count,
        }))
        return 3
    except (anydoc.ConvertError, OSError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "converted", "format": file_format, "path": relative_output.as_posix()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
