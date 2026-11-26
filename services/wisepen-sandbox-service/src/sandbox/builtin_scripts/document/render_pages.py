"""Render selected PDF or Office pages as PNG files in the workspace."""

import argparse
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import pypdfium2 as pdfium


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pages", required=True, help="One-based pages, e.g. 1,3-5")
    parser.add_argument("--dpi", type=int, default=144, choices=range(72, 301), metavar="72-300")
    args = parser.parse_args()
    workspace = Path.cwd().resolve()
    source = args.source.resolve()
    output = args.output.resolve()
    source.relative_to(workspace)
    output.relative_to(workspace)
    pages = set()
    for token in args.pages.split(","):
        first, separator, last = token.partition("-")
        start, end = int(first), int(last) if separator else int(first)
        if not 1 <= start <= end or end - start >= 20:
            parser.error("select at most 20 positive page numbers")
        pages.update(range(start, end + 1))
        if len(pages) > 20:
            parser.error("select at most 20 positive page numbers")

    suffix = source.suffix.lower()
    with TemporaryDirectory() as directory:
        scratch = Path(directory)
        pdf = source
        if suffix != ".pdf":
            # 导出隐藏幻灯片以保留原序号，独立 profile 允许并行转换。
            if suffix in {".ppt", ".pptx"}:
                export = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
            elif suffix in {".doc", ".docx"}:
                export = "pdf:writer_pdf_Export"
            elif suffix in {".xls", ".xlsx"}:
                export = "pdf:calc_pdf_Export"
            else:
                parser.error("unsupported render format")
            subprocess.run(
                ["soffice", f"-env:UserInstallation={(scratch / 'profile').as_uri()}",
                 "--headless", "--convert-to", export, "--outdir", str(scratch), str(source)],
                check=True, timeout=120, stdout=subprocess.DEVNULL,
            )
            pdf = scratch / f"{source.stem}.pdf"
        images = []
        with pdfium.PdfDocument(pdf) as document:
            if max(pages) > len(document):
                parser.error(f"document has {len(document)} renderable pages")
            output.mkdir(parents=True)
            for number in sorted(pages):
                page = document[number - 1]
                try:
                    # 大页面按像素上限缩放，避免 DPI 参数造成过大的位图。
                    scale = min(args.dpi / 72, 2400 / max(page.get_size()))
                    bitmap = page.render(scale=scale)
                    try:
                        destination = output / f"page-{number:04d}.png"
                        bitmap.to_pil().save(destination)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                images.append({"page": number, "path": destination.relative_to(workspace).as_posix()})
    print(json.dumps({"source": source.relative_to(workspace).as_posix(), "images": images}, ensure_ascii=False))


if __name__ == "__main__":
    main()
