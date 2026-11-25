"""Make a labeled contact sheet from explicitly ordered page images."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--columns", type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    if len(args.images) > 40 or args.output.suffix.lower() != ".png":
        parser.error("select at most 40 images and a new PNG output")
    workspace = Path.cwd().resolve()
    path = args.output.resolve().relative_to(workspace)
    columns = min(args.columns, len(args.images))
    rows = (len(args.images) + columns - 1) // columns
    width, height = 300, 250
    with Image.new("RGB", (columns * width, rows * height), "#eeeeee") as montage:
        draw = ImageDraw.Draw(montage)
        for index, source in enumerate(args.images):
            source.resolve().relative_to(workspace)
            row, column = divmod(index, columns)
            x, y = column * width, row * height
            with Image.open(source) as image:
                tile = ImageOps.contain(image.convert("RGBA"), (width - 16, height - 36))
                montage.paste(tile, (x + (width - tile.width) // 2, y + 8), tile)
            draw.text((x + 8, y + height - 22), f"{index + 1}. {source.stem[:32]}", fill="black")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            montage.save(output, format="PNG")
    print(json.dumps({"path": path.as_posix(), "images": len(args.images)}))


if __name__ == "__main__":
    main()
