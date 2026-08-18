"""从已发布 ReadingBlock 构建可精确回源的图抽取窗口。

抽取窗口是知识图谱抽取的最小上下文单元：
- 以 ReadingBlock 为归属边界，每个窗口属于且仅属于一个 ReadingBlock。
- 窗口文本可能比 ReadingBlock 短（ReadingBlock 太长时按 4000 字符 + 600 重叠切分），
  但每个窗口都保留完整的 source_mappings，能将窗口内任意字符偏移映射回原文坐标。
- 邻接 ReadingBlock 的尾部/头部作为“上下文”提供（不参与证据定位），帮助模型消歧。
"""

from dataclasses import dataclass, field

from rag.domain.repositories.mongo.published_resource_reader import GraphBuildSource
from common.utils.document import SourceSpan
from rag.utils.xml_markup import xml_attr, xml_cdata

# 邻接 ReadingBlock 提供给模型作为上下文的字符数（仅用于消歧，不可作为证据来源）。
_ADJACENT_CONTEXT_CHARACTERS = 800
# 单个抽取窗口的最大字符数。
_WINDOW_CHARACTERS = 4_000
# 相邻窗口之间的字符重叠量，避免跨窗口边界的关系/实体被切断。
_WINDOW_OVERLAP_CHARACTERS = 600


@dataclass(slots=True)
class ExtractionSourceMapping:
    """抽取窗口字符区间到权威 Markdown 字符区间的映射。

    window_start / window_end 是窗口局部坐标（从 0 开始）；
    source_span 是对应的权威 Markdown 字符偏移，可直接用于切片。
    """

    window_start: int
    window_end: int
    source_span: SourceSpan


@dataclass(slots=True)
class KnowledgeExtractionWindow:
    """一个 ReadingBlock 内可独立抽取并精确回源的窗口。

    字段说明：
    - text：窗口主文本（必属当前 ReadingBlock）。
    - source_mappings：窗口局部坐标到原文坐标的映射表。
    - previous_context / next_context：邻接 ReadingBlock 的上下文，仅作消歧，
      不可作为 evidence 来源。
    """

    resource_id: str
    content_revision: str
    reading_block_id: str
    section_path: list[str]
    window_id: str
    ordinal: int
    text: str
    source_mappings: list[ExtractionSourceMapping] = field(default_factory=list)
    previous_context: str = ""
    next_context: str = ""


def build_extraction_windows(
    source: GraphBuildSource,
) -> list[KnowledgeExtractionWindow]:
    """以 ReadingBlock 为归属边界构建窗口，邻接上下文不参与证据定位。

    流程：
    1. 对每个非空 ReadingBlock 计算其完整 source_mappings（局部坐标 → 原文坐标）。
    2. 按 4000 字符 + 600 重叠切分窗口；若 ReadingBlock 本身不超过 4000 字符，
       则整块作为一个窗口，window_id 直接复用 block_id 以保持稳定身份。
    3. 仅在窗口起点（start == 0）注入 previous_context，仅在窗口终点
       （end == len(raw_text)）注入 next_context，避免上下文重复。
    4. 同一 Section 内的邻接 block 才会被选作上下文，跨 Section 不连上下文。
    """
    sections_by_id = {
        section.section_id: section for section in source.structure.sections
    }
    windows: list[KnowledgeExtractionWindow] = []

    for block_index, block in enumerate(source.reading_blocks):
        if not block.raw_text.strip() or not block.source_spans:
            continue
        section = sections_by_id.get(block.section_id)
        if section is None:
            raise ValueError(f"reading block {block.block_id} has no section")

        # 计算 ReadingBlock 全量文本的局部→原文映射，供后续窗口裁剪复用。
        mappings = _source_mappings(source.markdown, block.raw_text, block.source_spans)
        # 邻接 ReadingBlock：仅同 Section 内的前/后块才作为上下文，避免跨主题污染。
        previous = source.reading_blocks[block_index - 1] if block_index else None
        next_block = (
            source.reading_blocks[block_index + 1]
            if block_index + 1 < len(source.reading_blocks)
            else None
        )
        for window_index, (start, end) in enumerate(
            _window_ranges(len(block.raw_text))
        ):
            # 整块作为一个窗口时直接复用 block_id 作为 window_id，简化身份；
            # 切分时使用 block_id:window:N 保持稳定且不冲突。
            whole_block = start == 0 and end == len(block.raw_text)
            windows.append(
                KnowledgeExtractionWindow(
                    resource_id=source.resource_id,
                    content_revision=source.content_revision,
                    reading_block_id=block.block_id,
                    section_path=list(section.section_path),
                    window_id=(
                        block.block_id
                        if whole_block
                        else f"{block.block_id}:window:{window_index}"
                    ),
                    ordinal=len(windows),
                    text=block.raw_text[start:end],
                    # 把全量 mappings 裁剪到当前窗口范围内，并转换为窗口局部坐标。
                    source_mappings=_clip_mappings(mappings, start, end),
                    # 仅在窗口起点注入 previous_context（避免每个切分窗口都重复携带）。
                    previous_context=(
                        previous.raw_text[-_ADJACENT_CONTEXT_CHARACTERS:]
                        if previous is not None
                        and previous.section_id == block.section_id
                        and start == 0
                        else ""
                    ),
                    # 仅在窗口终点注入 next_context。
                    next_context=(
                        next_block.raw_text[:_ADJACENT_CONTEXT_CHARACTERS]
                        if next_block is not None
                        and next_block.section_id == block.section_id
                        and end == len(block.raw_text)
                        else ""
                    ),
                )
            )
    return windows


def render_extraction_window(window: KnowledgeExtractionWindow) -> str:
    """渲染 GraphRAG 输入，并明确 evidence 只能来自当前窗口。

    渲染规则：
    - 用 XML 标签包裹各部分内容，xml_attr / xml_cdata 转义特殊字符。
    - EXTRACTION_RULES 明确告诉模型：evidence_quote 必须是
      <current_reading_block> 的连续子串；previous/next 仅供消歧。
    - 资源 ID 必须原样复制，避免模型臆造。
    """
    section_path = " > ".join(window.section_path) or "(document root)"
    return f"""EXTRACTION_RULES:
- Extract only facts supported by <current_reading_block>.
- evidence_quote must be one exact continuous substring of <current_reading_block>.
- Previous and next context are only for disambiguation.
- Use the current Resource node and copy resource_id exactly.
- RELATED_TO requires a specific predicate.

<extraction_window resource_id="{xml_attr(window.resource_id)}">
  <section_path>{xml_cdata(section_path)}</section_path>
  <previous_context>{xml_cdata(window.previous_context)}</previous_context>
  <current_reading_block>{xml_cdata(window.text)}</current_reading_block>
  <next_context>{xml_cdata(window.next_context)}</next_context>
</extraction_window>
"""


def _source_mappings(
    markdown: str,
    raw_text: str,
    source_spans: list[SourceSpan],
) -> list[ExtractionSourceMapping]:
    """计算 ReadingBlock 全量文本的局部坐标 → 原文坐标映射表。

    利用 raw_text 是 source_spans 用 \\n\\n 拼接而成的事实：
    按 source_spans 顺序在 raw_text 中查找每个 span 对应的原文子串，
    记录其在 raw_text 中的局部起止位置。

    cursor 用于避免同一子串在 raw_text 中多次出现时被错误地映射到第一次出现的位置。
    """
    mappings: list[ExtractionSourceMapping] = []
    cursor = 0
    for source_span in source_spans:
        source_text = markdown[source_span.start_offset : source_span.end_offset]
        # 在 raw_text 中从 cursor 开始查找原文子串，保证按顺序匹配。
        local_start = raw_text.find(source_text, cursor)
        if not source_text or local_start < 0:
            raise ValueError("reading block source span does not match raw text")
        local_end = local_start + len(source_text)
        mappings.append(
            ExtractionSourceMapping(
                window_start=local_start,
                window_end=local_end,
                source_span=source_span,
            )
        )
        cursor = local_end
    return mappings


def _window_ranges(text_length: int) -> list[tuple[int, int]]:
    """按 4000 字符 + 600 重叠切分 ReadingBlock，返回 (start, end) 区间列表。

    若文本不超过窗口大小，则返回单个覆盖全量的区间；
    否则按 step = window - overlap 推进起点，最后一个区间终点固定为 text_length。
    """
    if text_length <= _WINDOW_CHARACTERS:
        return [(0, text_length)]

    ranges: list[tuple[int, int]] = []
    start = 0
    step = _WINDOW_CHARACTERS - _WINDOW_OVERLAP_CHARACTERS
    while start < text_length:
        end = min(start + _WINDOW_CHARACTERS, text_length)
        ranges.append((start, end))
        # 已覆盖到末尾则停止，避免多生成一个空区间。
        if end == text_length:
            break
        start += step
    return ranges


def _clip_mappings(
    mappings: list[ExtractionSourceMapping],
    window_start: int,
    window_end: int,
) -> list[ExtractionSourceMapping]:
    """把全量 mappings 裁剪到 [window_start, window_end] 范围，并转换为窗口局部坐标。

    处理细节：
    - 与窗口不相交的 mapping 直接跳过。
    - 部分相交的 mapping 会被裁剪到交集范围，原文坐标同步调整。
    - 输出的 window_start / window_end 已减去 window_start 偏移，
      使其从 0 开始，便于窗口内文本直接索引。
    """
    clipped: list[ExtractionSourceMapping] = []
    for mapping in mappings:
        start = max(mapping.window_start, window_start)
        end = min(mapping.window_end, window_end)
        if start >= end:
            continue
        # 原文起点 = mapping 原文起点 + (裁剪后局部起点 - mapping 局部起点)
        source_start = mapping.source_span.start_offset + start - mapping.window_start
        clipped.append(
            ExtractionSourceMapping(
                window_start=start - window_start,
                window_end=end - window_start,
                source_span=SourceSpan(source_start, source_start + end - start),
            )
        )
    return clipped
