from __future__ import annotations

from chat.application.tools.common.evidence_ranking.models import EvidenceRankResult


def format_evidence_result(result: EvidenceRankResult) -> str:
    """
    精排黄金资产文本格式化渲染器。
    高保真吐出面向大模型消费的结构化证据文本，并动态注入下一步工具决策链提示词（Tool Call Prompt Tips）。
    """
    lines = [
        "[Tool Result] Ranked Evidence",
        f"Query: {result.query}",
        f"Scanned {result.total_chunks_scanned} chunks from {len(result.content_ids_found)} content source(s).",
    ]

    if result.content_ids_missing:
        lines.append(f"Missing/expired content_ids: {', '.join(result.content_ids_missing)}")

    # 无任何有效召回证据
    if not result.evidence:
        lines.append("\nNo relevant evidence found for the given query.")
        if result.notes:
            lines.extend(["Notes:", *(f"- {note}" for note in result.notes)])
        return "\n".join(lines)

    lines.append(f"\nRanked Evidence ({len(result.evidence)} snippet(s)):")

    # 循环渲染证据细节
    for display_index, ev in enumerate(result.evidence, 1):
        lines.extend([
            f"\n[{display_index}]",
            f"   Raw rank: {ev.rank + 1}",
            f"   Title: {ev.display_title}",
        ])
        if ev.source_id:
            lines.append(f"   source_id: {ev.source_id}")
        if ev.domain:
            lines.append(f"   Domain: {ev.domain}")
        if ev.url:
            lines.append(f"   URL: {ev.url}")

        lines.append(f"   content_id: {ev.content_id}")

        if ev.chunk_index >= 0:
            lines.extend([
                f"   chunk_index: {ev.chunk_index}",
                f"   start_offset: {ev.start_offset}",
                f"   end_offset: {ev.end_offset}",
            ])
        if ev.evidence_type:
            lines.append(f"   Evidence type: {ev.evidence_type}")

        lines.append(f"   Score: {ev.score:.4f}")

        if ev.term_hit_stats:
            lines.append("   Term hit stats:")
            for term_stat in ev.term_hit_stats:
                field_text = ", ".join(f"{fs.field}={fs.count}" for fs in term_stat.field_stats)
                lines.append(
                    f"      - {term_stat.term}: total={term_stat.total_count}; {field_text}"
                    if field_text else f"      - {term_stat.term}: total={term_stat.total_count}"
                )

        if ev.matched_reason:
            lines.append(f"   Matched reason: {ev.matched_reason}")

        if ev.excerpt:
            lines.append("   Excerpt:")
            lines.extend(f"      {excerpt_line}" for excerpt_line in ev.excerpt.split("\n"))

        if ev.context_preview:
            lines.append("   Context preview:")
            for key in [
                "before",
                "after",
                "current_chunk_index",
                "start_chunk_index",
                "end_chunk_index",
                "truncated",
            ]:
                if key in ev.context_preview:
                    lines.append(f"      {key}: {ev.context_preview[key]}")
            preview_text = ev.context_preview.get("text")
            if isinstance(preview_text, str) and preview_text:
                lines.append("      text: |-")
                lines.extend(f"        {line}" for line in preview_text.splitlines())

    lines.append("")

    # 特征分析：为下游大模型动态推导 Tool Tips 诱导词
    has_web_search_result = any(ev.evidence_type == "web_search_result" for ev in result.evidence)
    chunk_evidence = [ev for ev in result.evidence if ev.chunk_index >= 0]

    if has_web_search_result:
        lines.append(
            "These ranked items are search-result snippets, not fetched page bodies. "
            "For technical details, direct quotes, conflict resolution, or source verification, "
            "call web_fetch with from_search_content_id set to the ranked content_id and "
            "source_ids set to the selected source_id values."
        )

    if chunk_evidence:
        lines.extend([
            "To inspect surrounding content for chunk evidence, call tool_content_read with content_id, "
            "chunk_index, before_chunks=1, and after_chunks=1. Example:",
            f'tool_content_read({{"content_id": "{chunk_evidence[0].content_id}", "chunk_index": {chunk_evidence[0].chunk_index}, "before_chunks": 1, "after_chunks": 1}})'
        ])

        if len(chunk_evidence) >= 2:
            examples = chunk_evidence[:3]
            lines.extend([
                "Batch expand example for related chunk evidence. Use this only when these "
                "chunk evidence items are thematically related and need to be inspected together:",
                'tool_content_batch_read({"items": ['
            ])
            lines.extend(
                f'  {{"content_id": "{ev.content_id}", "chunk_index": {ev.chunk_index}, "before_chunks": 1, "after_chunks": 1}}{"," if i < len(examples) - 1 else ""}'
                for i, ev in enumerate(examples)
            )
            lines.append('], "max_total_chars": 12000})')

    if not has_web_search_result and not chunk_evidence:
        lines.append("To inspect surrounding content, call tool_content_read with content_id and offset.")

    if result.notes:
        lines.extend(["Notes:", *(f"- {note}" for note in result.notes)])

    return "\n".join(lines)
