"""展示解析后的嵌套节点、原文字符范围和结构 metadata。"""

import json
from dataclasses import asdict
from pathlib import Path

from common.utils.markdown import MarkdownParser


def main() -> None:
    demo_dir = Path(__file__).resolve().parent
    source = (demo_dir / "sample.md").read_text(encoding="utf-8")
    nodes = MarkdownParser().parse(source)
    output = demo_dir / "results" / "parser.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "source": "../sample.md",
                "source_span_contract": "Python 字符半开区间 [start_offset, end_offset)",
                "nodes": [asdict(node) for node in nodes],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Parser: {len(nodes)} 个根节点 → {output}")


if __name__ == "__main__":
    main()
