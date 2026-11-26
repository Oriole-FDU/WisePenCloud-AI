"""List addressable PowerPoint objects, notes and geometry by slide."""

import argparse
import json
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def describe(shapes):
    for shape in shapes:
        item = {
            "id": shape.shape_id,
            "name": shape.name,
            "type": shape.shape_type.name,
            "bounds_emu": [shape.left, shape.top, shape.width, shape.height],
        }
        if shape.has_text_frame:
            item["text"] = shape.text[:300]
        if shape.has_table:
            item["table"] = {"rows": len(shape.table.rows), "columns": len(shape.table.columns)}
        if shape.has_chart:
            item["chart"] = {"type": shape.chart.chart_type.name,
                             "series": [series.name for series in shape.chart.series]}
        yield item
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            # 子对象坐标属于组，组的边界仍是相对幻灯片的。
            for child in describe(shape.shapes):
                child.setdefault("parent_id", shape.shape_id)
                yield child


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--slide", type=int)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    args.source.resolve().relative_to(Path.cwd().resolve())
    deck = Presentation(args.source)
    if args.offset < 0:
        parser.error("offset must be nonnegative")
    if args.slide is None:
        pages = range(
            args.offset, min(args.offset + 30, len(deck.slides)))
        entries = []
        for index in pages:
            title = deck.slides[index].shapes.title
            entries.append({"slide": index + 1, "title": title.text[:300] if title is not None else ""})
        result = {"slides": entries, "slide_count": len(deck.slides),
                  "next_offset": args.offset + len(entries) if args.offset + len(entries) < len(deck.slides) else None}
    else:
        if not 1 <= args.slide <= len(deck.slides):
            parser.error(f"slide must be between 1 and {len(deck.slides)}")
        slide = deck.slides[args.slide - 1]
        notes = slide.notes_slide.notes_text_frame if slide.has_notes_slide else None
        objects = list(describe(slide.shapes))
        result = {"slide": args.slide, "notes_preview": notes.text[:1000] if notes is not None else "",
                  "objects": objects[args.offset:args.offset + 30],
                  "next_offset": args.offset + 30 if args.offset + 30 < len(objects) else None}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
