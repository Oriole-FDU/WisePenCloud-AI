from __future__ import annotations

from typing import Any


def find_opened_note_resource(frontend_states: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for state in frontend_states or []:
        if state.get("disabled", False) or state.get("key") != "workspace_open_resource":
            continue
        value = state.get("value")
        if not isinstance(value, dict):
            continue
        resource_type = str(value.get("resource_type") or "").lower()
        viewer = str(value.get("viewer") or "").lower()
        if resource_type == "note" or viewer == "note":
            return value
    return None


__all__ = ["find_opened_note_resource"]
