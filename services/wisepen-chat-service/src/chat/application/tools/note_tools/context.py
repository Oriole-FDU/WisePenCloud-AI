from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class NoteToolContext:
    resource_id: str
    editor_type: str
    selected_text: str | None = None
    selected_scope: dict[str, Any] | None = None
    client_state_vector: str | None = None


def resolve_note_tool_context(
    frontend_states: Sequence[Mapping[str, Any]] | None,
) -> NoteToolContext | None:
    all_states = [
        state
        for state in (frontend_states or [])
        if isinstance(state, Mapping)
        and state.get("value") is not None
    ]
    active_states = [state for state in all_states if not bool(state.get("disabled"))]

    workspace_value = _state_value(active_states, "workspace_open_resource")
    if not isinstance(workspace_value, Mapping):
        return None

    resource_id = _read_string(workspace_value, "resource_id", "resourceId")
    editor_type = _read_string(workspace_value, "editor_type", "editorType").lower()
    if not resource_id or editor_type != "note":
        return None

    selected_text_value = _state_value(active_states, "selected_text")
    selected_text = selected_text_value.strip() if isinstance(selected_text_value, str) else None
    selected_text = selected_text or None

    selected_scope_value = _state_value(active_states, "selected_note_scope")
    selected_scope = dict(selected_scope_value) if isinstance(selected_scope_value, Mapping) else None

    client_state_vector_value = _state_value(all_states, "note_client_state_vector")
    client_state_vector = (
        client_state_vector_value.strip()
        if isinstance(client_state_vector_value, str)
        else None
    )
    client_state_vector = client_state_vector or None

    return NoteToolContext(
        resource_id=resource_id,
        editor_type=editor_type,
        selected_text=selected_text,
        selected_scope=selected_scope,
        client_state_vector=client_state_vector,
    )


def _state_value(states: Sequence[Mapping[str, Any]], key: str) -> Any:
    for state in states:
        if state.get("key") == key:
            return state.get("value")
    return None


def _read_string(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
