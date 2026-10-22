from __future__ import annotations

from typing import Any

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_NOTE_SERVICE_NAME = "wisepen-note-service"
_CREATE_NOTE_PATH = "/note/addNote"
_GET_NOTE_SEARCH_TEXT_PATH = "/internal/note/getNoteSearchText"


class NoteClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _NOTE_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def create_note(
        self,
        *,
        title: str,
        mount_target_tag_id: str | None = None,
    ) -> str:
        request: dict[str, Any] = {"title": title}
        if mount_target_tag_id is not None:
            request["mountTargetTagId"] = mount_target_tag_id
        data = await self._rpc.post(
            self._service_name,
            _CREATE_NOTE_PATH,
            json=request,
        )
        if not isinstance(data, str) or not data.strip():
            raise RpcError(
                service_name=self._service_name,
                path=_CREATE_NOTE_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data.strip()

    async def get_search_text(self, resource_id: str) -> dict[str, Any]:
        data = await self._rpc.get(
            self._service_name,
            _GET_NOTE_SEARCH_TEXT_PATH,
            params={"resourceId": resource_id},
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_NOTE_SEARCH_TEXT_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data


__all__ = ["NoteClient"]
