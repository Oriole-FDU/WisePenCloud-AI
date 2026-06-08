from typing import List, Optional, Set


def add_note(notes: Optional[List[str]], note: str) -> None:
    """向备注列表中添加一条规范化后的备注。

    自动折叠空白、去重，notes 为 None 时静默跳过。

    Args:
        notes: 备注列表（可为 None）。
        note: 待添加的备注文本。
    """
    if notes is None:
        return

    normalized = " ".join(note.strip().split())
    if not normalized or normalized in notes:
        return

    notes.append(normalized)


def deduplicate_notes(notes: List[str]) -> List[str]:
    """对备注列表执行去重，保留首次出现的顺序。

    Args:
        notes: 原始备注列表。

    Returns:
        去重后的备注列表。
    """
    seen: Set[str] = set()
    deduped: List[str] = []
    for note in notes:
        normalized = " ".join(note.strip().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped
