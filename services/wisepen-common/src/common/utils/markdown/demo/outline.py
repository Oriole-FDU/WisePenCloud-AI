"""展示章节和锚点如何投影为不含内部字符范围的精简目录。"""

import json
from pathlib import Path

from common.utils.markdown import MarkdownChunker, OutlineFormatter


def main() -> None:
    demo_dir = Path(__file__).resolve().parent
    source = (demo_dir / "sample.md").read_text(encoding="utf-8")
    result = MarkdownChunker().chunk(source, title="研究笔记")
    # Global 和 neighborhood 共用生产环境的 Markdown 格式化器。
    formatter = OutlineFormatter(sections=result.sections, anchors=result.anchors)
    current_section = next(
        section for section in result.sections if section.title == "表格与图片"
    )
    output = demo_dir / "results" / "outline.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "source": "../sample.md",
                "format_contract": "按标题树深度输出 #；行尾依次为 [C]、锚点、原始 section id 和原文起始字符偏移",
                "global_max_level_2": formatter.global_outline(max_level=2),
                "global_all_levels": formatter.global_outline(),
                "neighborhood": {
                    "current_section_id": current_section.section_id,
                    "sibling_steps": 1,
                    "markdown": formatter.neighborhood(
                        current_section.section_id,
                        sibling_steps=1,
                    ),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Outline: 全局目录和邻域格式化结果 → {output}")


if __name__ == "__main__":
    main()
