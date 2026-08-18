"""直接使用 Common parser/chunker/outline assembler 输出 test1.md 目录。"""

import json
from dataclasses import asdict
from pathlib import Path

from common.utils.document import OutlineAssembler
from rag.application.rag.index.constructor import build_document_structure


def main() -> None:
    markdown = Path(__file__).with_name("test1.md").read_text(encoding="utf-8")
    structure = build_document_structure(markdown)
    outline = OutlineAssembler().assemble(
        sections=structure.sections,
        pages=structure.pages,
        anchors=structure.anchors,
    )
    output = json.dumps(
        [asdict(node) for node in outline],
        ensure_ascii=False,
        indent=2,
    )
    output_path = Path(__file__).with_name("test1_outline.txt")
    output_path.write_text(output, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
