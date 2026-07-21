from __future__ import annotations

from collections.abc import Mapping

_PAGE_MARKER_PREFIX = "<!-- page "
_PAGE_MARKER_TEMPLATE = "<!-- page {page_number} -->\n\n"
_NON_BODY_BLOCK_TYPES = frozenset(
    {
        "header",
        "footer",
        "page_number",
        "aside_text",
        "page_footnote",
    }
)
_ANCHOR_FIELDS = (
    "text",
    "img_path",
    "content",
    "image_caption",
    "image_footnote",
    "table_caption",
    "table_body",
    "table_footnote",
    "code_caption",
    "code_body",
    "code_footnote",
    "list_items",
)


def insert_page_markers(
    markdown: str,
    content_list: object,
) -> str:
    """在所有正文页面都能可靠定位时插入页码标记。"""
    # 已包含标记或缺少完整 content_list 时不猜测页码，避免产生错误定位。
    if _PAGE_MARKER_PREFIX in markdown or not isinstance(content_list, list):
        return markdown

    page_anchors: dict[int, tuple[str, ...]] = {}
    for item in content_list:
        if not isinstance(item, Mapping):
            return markdown

        page_idx = item.get("page_idx")
        if type(page_idx) is not int or page_idx < 0:
            return markdown

        if (
            page_idx in page_anchors
            or item.get("type") in _NON_BODY_BLOCK_TYPES
        ):
            continue

        anchors: list[str] = []
        seen: set[str] = set()
        for field in _ANCHOR_FIELDS:
            value = item.get(field)
            candidates = value if isinstance(value, list) else (value,)

            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue

                anchor = candidate.strip()
                if anchor and anchor not in seen:
                    seen.add(anchor)
                    anchors.append(anchor)

        if anchors:
            page_anchors[page_idx] = tuple(anchors)

    if not page_anchors:
        return markdown

    marker_positions: list[tuple[int, int]] = []
    previous_position = -1
    for page_idx, anchors in sorted(page_anchors.items()):
        position = _find_block_start(markdown, anchors)
        # 锚点必须能按页面顺序唯一定位；重复正文会让页码失去可信度。
        if position is None or position <= previous_position:
            return markdown

        marker_positions.append((position, page_idx + 1))
        previous_position = position

    parts: list[str] = []
    cursor = 0
    for position, page_number in marker_positions:
        parts.append(markdown[cursor:position])
        parts.append(
            _PAGE_MARKER_TEMPLATE.format(page_number=page_number)
        )
        cursor = position

    parts.append(markdown[cursor:])
    return "".join(parts)


def _find_block_start(
    markdown: str,
    anchors: tuple[str, ...],
) -> int | None:
    positions: list[int] = []
    for anchor in anchors:
        position = markdown.find(anchor)
        if position < 0:
            continue

        if markdown.find(anchor, position + len(anchor)) < 0:
            positions.append(position)

    if not positions:
        return None

    anchor_position = min(positions)
    lf_separator = markdown.rfind("\n\n", 0, anchor_position)
    crlf_separator = markdown.rfind("\r\n\r\n", 0, anchor_position)
    if crlf_separator > lf_separator:
        return crlf_separator + 4

    return lf_separator + 2 if lf_separator >= 0 else 0
