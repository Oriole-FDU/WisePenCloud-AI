from __future__ import annotations

from sandbox.core.storage.memory.state import _RepositoryState
from sandbox.domain.entities import (
    SessionWorkspaceRecord,
    utc_now,
)


class MemoryWorkspaceManager:
    """In-memory session-workspace operations."""

    def __init__(self, state: _RepositoryState) -> None:
        self._state = state

    # -- factories called by the composite checkout -----------------------

    def _upsert_workspace(
        self,
        user_id: str,
        session_id: str,
        sandbox_id: str,
        container_generation: int,
        *,
        reused: bool,
    ) -> SessionWorkspaceRecord:
        session_key = (user_id, session_id)
        if reused:
            workspace = self._state.workspaces[session_key]
        else:
            workspace = SessionWorkspaceRecord(
                user_id=user_id,
                session_id=session_id,
                sandbox_id=sandbox_id,
                container_generation=container_generation,
            )
            self._state.workspaces[session_key] = workspace
        return workspace

    # -- public read / query -------------------------------------------------

    async def find_workspace(
        self, user_id: str, session_id: str
    ) -> SessionWorkspaceRecord | None:
        async with self._state.lock:
            return self._state.workspaces.get((user_id, session_id))

    async def workspaces_for_user(self, user_id: str) -> list[SessionWorkspaceRecord]:
        async with self._state.lock:
            return [
                workspace
                for key, workspace in self._state.workspaces.items()
                if key[0] == user_id
            ]

    # -- lifecycle -----------------------------------------------------------

    async def mark_workspace_prepared(
        self, user_id: str, session_id: str
    ) -> SessionWorkspaceRecord:
        async with self._state.lock:
            workspace = self._state.workspaces[(user_id, session_id)]
            workspace.updated_at = utc_now()
            workspace.last_error = None
            self._state.generation += 1
            return workspace

    async def mark_workspace_dirty(self, user_id: str, session_id: str) -> None:
        async with self._state.lock:
            workspace = self._state.workspaces.get((user_id, session_id))
            if workspace:
                workspace.dirty = True
                workspace.updated_at = utc_now()
                self._state.generation += 1

    async def remove_workspace(self, user_id: str, session_id: str) -> bool:
        async with self._state.lock:
            removed = (
                self._state.workspaces.pop((user_id, session_id), None) is not None
            )
            self._state.generation += int(removed)
            return removed
