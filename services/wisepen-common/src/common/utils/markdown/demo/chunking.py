"""展示 token 预算、结构拆分、章节归属和不可拆单元的 overflow。"""

import json
from dataclasses import asdict
from pathlib import Path

from common.utils.markdown import MarkdownChunker


def main() -> None:
    demo_dir = Path(__file__).resolve().parent
    source = (demo_dir / "sample.md").read_text(encoding="utf-8")
    # 使用较小预算，让短样例也能展示列表、表格和代码的拆分效果。
    budgets = {"target_chunk_tokens": 80, "split_threshold_tokens": 160}
    result = MarkdownChunker(
        budgets["target_chunk_tokens"], budgets["split_threshold_tokens"]
    ).chunk(source)
    output_dir = demo_dir / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "chunking.json").write_text(
        json.dumps(
            {
                "source": "../sample.md",
                "config": budgets,
                "chunks": [asdict(chunk) for chunk in result.chunks],
                "sections": [asdict(section) for section in result.sections],
                "anchors": [asdict(anchor) for anchor in result.anchors],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = [
        "# Chunking 示例结果",
        "",
        "输入：[sample.md](../sample.md)。完整结构字段见 [chunking.json](chunking.json)。",
        "",
        f"目标：{budgets['target_chunk_tokens']} tokens；拆分阈值：{budgets['split_threshold_tokens']} tokens。",
        "计数器：离线 o200k_base。不可安全拆分的结构可以超过阈值，以 overflow 标识。",
        "",
        f"共 {len(result.chunks)} 个 chunk，{len(result.sections)} 个 section，{len(result.anchors)} 个 anchor。",
    ]
    for chunk in result.chunks:
        report.extend(
            [
                "",
                f"## Chunk {chunk.chunk_index}",
                "",
                f"- 章节：{' / '.join(chunk.section_path) or '无标题正文'}",
                f"- 内容 tokens：{chunk.content_token_count}",
                f"- Overflow：{chunk.overflow}",
                f"- 原始节点：{', '.join(chunk.node_ids)}",
                f"- 锚点：{', '.join(chunk.anchor_labels) or '无'}",
                "",
                # 四个反引号包裹原始 Markdown，样例中的三反引号代码块不会提前闭合。
                "````markdown",
                chunk.text,
                "````",
            ]
        )
    output = output_dir / "chunking.md"
    output.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Chunking: {len(result.chunks)} 个 chunk → {output}")


if __name__ == "__main__":
    main()
