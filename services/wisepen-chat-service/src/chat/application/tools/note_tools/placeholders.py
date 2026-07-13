from typing import Any


def build_note_ai_xml_placeholder(tool_call_arguments: dict[str, Any], output: Any) -> str:
    export_handle = _extract_after("export_handle=", output)
    expires_at = _extract_after("expires_at=", output)
    suffix = []
    if export_handle:
        suffix.append(f"export_handle={export_handle}")
    if expires_at:
        suffix.append(f"expires_at={expires_at}")
    details = f" {' '.join(suffix)}" if suffix else ""
    return (
        "[Note AI XML omitted from persistent history."
        f"{details}. "
        "If the note content is needed again in a later turn, call read_note_aixml again.]"
    )


def build_note_ai_apply_placeholder(tool_call_arguments: dict[str, Any], output: Any) -> str:
    export_handle = tool_call_arguments.get("export_handle") or "unknown"
    return (
        "[Note AI-Diff apply result omitted from persistent history. "
        f"export_handle={export_handle}.]"
    )


def _extract_after(prefix: str, output: Any) -> str:
    if not isinstance(output, str):
        return ""
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""
