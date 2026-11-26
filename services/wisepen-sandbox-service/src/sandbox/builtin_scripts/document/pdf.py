"""Inspect and fill AcroForms, or place text on a static PDF using page-relative boxes."""

import argparse
from collections import defaultdict
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

from pypdf import PdfReader, PdfWriter


def form_fields(reader):
    for name, field in (reader.get_fields() or {}).items():
        kind = str(field.get("/FT", ""))
        if kind == "/Sig" or kind not in {"/Tx", "/Btn", "/Ch"}:
            continue
        choices = field.get("/_States_") or field.get("/Opt") or []
        yield {"name": name, "type": kind, "value": field.get("/V"),
               "pages": [page.page_number + 1 for page in reader.get_pages_showing_field(field)],
               "choices": [str(choice[0] if isinstance(choice, list) else choice) for choice in choices]}


def fill_widgets(reader, values):
    fields = {field["name"]: field for field in form_fields(reader)}
    if not isinstance(values, dict) or not values:
        raise ValueError("provide a JSON object mapping field names to values")
    pages = set()
    for name, value in values.items():
        if name not in fields or not fields[name]["pages"]:
            raise ValueError(f"no field widget named {name!r}")
        field = fields[name]
        if not isinstance(value, str):
            raise ValueError(f"{name!r} requires a string value")
        if field["choices"] and value not in field["choices"]:
            raise ValueError(f"{name!r} accepts {field['choices']}")
        pages.update(field["pages"])
    writer = PdfWriter(clone_from=reader)
    for number in sorted(pages):
        writer.update_page_form_field_values(writer.pages[number - 1], values, auto_regenerate=False)
    return writer, sorted(pages)


def overlay_boxes(reader, entries, preview, family):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if not isinstance(entries, list) or not entries:
        raise ValueError("provide a JSON array of page boxes")
    by_page = defaultdict(list)
    for entry in entries:
        number = entry["page"]
        x, y, width, height = entry["box"]
        if type(number) is not int or not 1 <= number <= len(reader.pages):
            raise ValueError(f"invalid page: {number}")
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 - x and 0 < height <= 1 - y):
            raise ValueError(f"page {number}: box must fit inside the page")
        by_page[number].append(entry)
    if not preview:
        match = subprocess.run(
            ["fc-match", "-f", "%{file}\n%{index}\n", family],
            check=True, capture_output=True, text=True, timeout=10,
        )
        font_file, font_index = match.stdout.splitlines()
    writer = PdfWriter(clone_from=reader)
    for number, boxes in by_page.items():
        page = writer.pages[number - 1]
        # 先把旋转表达到页面内容中，再以旋转后的 CropBox 对齐模型看到的页图。
        page.transfer_rotation_to_content()
        crop = page.cropbox
        width, height = float(crop.width), float(crop.height)
        buffer = BytesIO()
        overlay = canvas.Canvas(buffer, pagesize=(width, height))
        for index, entry in enumerate(boxes, 1):
            x, y, w, h = entry["box"]
            left, bottom = x * width, (1 - y - h) * height
            w, h = w * width, h * height
            if preview:
                overlay.setStrokeColorRGB(0.85, 0.1, 0.15)
                overlay.setFillColorRGB(0.85, 0.1, 0.15)
                overlay.rect(left, bottom, w, h, fill=0)
                overlay.drawString(left + 2, bottom + 2, str(index))
            else:
                text = entry["text"]
                size = float(entry.get("font_size", 11))
                if not 1 <= size <= 144 or not isinstance(text, str):
                    raise ValueError(f"page {number} box {index}: use text and font size 1–144 pt")
                # Pillow 可直接使用离线 CJK 字体文件及 TTC 字体索引。只栅格化新增文字。
                scale = 3
                font = ImageFont.truetype(font_file, round(size * scale), index=int(font_index))
                image = Image.new("RGBA", (max(1, round(w * scale)), max(1, round(h * scale))))
                draw = ImageDraw.Draw(image)
                x0, y0, x1, y1 = draw.multiline_textbbox((0, 0), text, font=font)
                if x1 - x0 > image.width or y1 - y0 > image.height:
                    raise ValueError(f"page {number} box {index}: enlarge the box or reduce font_size")
                draw.multiline_text((-x0, -y0), text, font=font, fill="black")
                overlay.drawImage(ImageReader(image), left, bottom, w, h, mask="auto")
        overlay.showPage()
        overlay.save()
        buffer.seek(0)
        page.merge_translated_page(PdfReader(buffer).pages[0], float(crop.left), float(crop.bottom), over=True)
    return writer, sorted(by_page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("inspect")
    for name in ("fill", "preview-boxes", "fill-boxes"):
        command = commands.add_parser(name)
        command.add_argument("data", type=Path)
        command.add_argument("output", type=Path)
        if name == "fill-boxes":
            command.add_argument("--font", default="Noto Sans CJK SC")
    args = parser.parse_args()
    workspace = Path.cwd().resolve()
    args.source.resolve().relative_to(workspace)
    reader = PdfReader(args.source)
    if args.operation == "inspect":
        result = {"fields": list(form_fields(reader)), "page_count": len(reader.pages)}
    else:
        args.data.resolve().relative_to(workspace)
        output = args.output.resolve()
        path = output.relative_to(workspace)
        data = json.loads(args.data.read_text(encoding="utf-8-sig"))
        if args.operation == "fill":
            writer, pages = fill_widgets(reader, data)
        else:
            writer, pages = overlay_boxes(reader, data, args.operation == "preview-boxes",
                                          getattr(args, "font", None))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            writer.write(target)
        result = {"path": path.as_posix(), "pages": pages}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(str(exc))
