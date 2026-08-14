from __future__ import annotations

from typing import Any

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_NOTE_SERVICE_NAME = "wisepen-note-service"
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
