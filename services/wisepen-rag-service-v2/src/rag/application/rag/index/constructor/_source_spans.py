"""constructor 内部使用的原文 span 渲染和映射规则。

本模块处理两类坐标之间的转换：
- markdown 原文坐标：直接对应权威 Markdown 字符串的 Python 字符偏移。
- rendered 渲染坐标：把若干 SourceSpan 切片用 \\n\\n 拼接后形成的
  “虚拟文本”中的偏移；用于把 chunker 在拼接文本上切出来的子块重新映射回原文。
"""

from common.utils.document import SourceSpan


def _render_source_text(markdown: str, source_spans: list[SourceSpan]) -> str:
    """按照 source_spans 范围渲染原文片段。"""
    return "\n\n".join(
        markdown[span.start_offset : span.end_offset] for span in source_spans
    )


def _map_rendered_spans_to_source(
    *,
    rendered_spans: list[SourceSpan],
    source_spans: list[SourceSpan],
) -> list[SourceSpan]:
    """把渲染坐标下的 rendered_spans 反向映射回原文坐标。
    Args:
        rendered_spans: chunker 在渲染文本上产出的偏移区间。
        source_spans: 渲染文本对应的原文片段列表（按渲染顺序排列）。

    Returns:
        去重后的原文 SourceSpan 列表；当某个 rendered_span 跨越多个 source 片段时，
        会被拆成多段分别映射。
    """
    mapped: list[SourceSpan] = []
    # rendered_cursor 跟踪"当前 source 片段"在渲染文本中的起始偏移。
    rendered_cursor = 0

    for source_span in source_spans:
        source_length = source_span.end_offset - source_span.start_offset
        # 当前 source 片段在渲染文本中占据 [rendered_cursor, rendered_end) + 换行符。
        rendered_end = rendered_cursor + source_length
        for rendered_span in rendered_spans:
            # 跳过与当前 source 片段渲染区间不相交的 rendered_span。
            if (
                rendered_span.start_offset >= rendered_end
                or rendered_span.end_offset <= rendered_cursor
            ):
                continue

            # 把 rendered_span 裁剪到当前 source 片段的渲染区间内，
            mapped_span = SourceSpan(
                source_span.start_offset
                + max(rendered_span.start_offset, rendered_cursor)
                - rendered_cursor,
                source_span.start_offset
                + min(rendered_span.end_offset, rendered_end)
                - rendered_cursor,
            )
            # 同一个 span 可能被多个 source 片段命中（跨片段 rendered_span），去重。
            if mapped_span not in mapped:
                mapped.append(mapped_span)
        # 推进游标：当前片段长度 + 2 个换行符字符（与 \n\n 对应）。
        rendered_cursor = rendered_end + 2

    return mapped


def _overlaps(target: SourceSpan, source_spans: list[SourceSpan]) -> bool:
    """判断 target 区间是否与任意一个 source_span 存在非空交集。"""
    return any(
        span.start_offset < target.end_offset and span.end_offset > target.start_offset
        for span in source_spans
    )
