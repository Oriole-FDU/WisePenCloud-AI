from __future__ import annotations

from typing import Any, Mapping

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-note-collab-service"
_READ_XML_PATH = "/internal/ai-note/readXml"
_APPLY_PATH = "/internal/ai-note/apply"


class NoteCollabClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
        read_timeout: float = 10.0,
        apply_timeout: float = 15.0,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name
        self._read_timeout = read_timeout
        self._apply_timeout = apply_timeout

    async def read_note_xml(self, resource_id: str, request: Mapping[str, Any]) -> str:
        response = await self._rpc.request_raw(
            "POST",
            self._service_name,
            _READ_XML_PATH,
            params={"resourceId": resource_id},
            json=dict(request),
            timeout=self._read_timeout,
        )
        if response.status_code < 200 or response.status_code >= 300:
            msg = response.text[:500]
            code: int | None = None
            try:
                body = response.json()
                if isinstance(body, dict):
                    msg = str(body.get("msg") or msg)
                    code = int(body["code"]) if "code" in body else None
            except Exception:
                pass
            raise RpcError(
                service_name=self._service_name,
                path=_READ_XML_PATH,
                status=response.status_code,
                code=code,
                msg=msg,
            )
        return response.text

    async def apply_note_ai_diff(self, resource_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        data = await self._rpc.post(
            self._service_name,
            _APPLY_PATH,
            params={"resourceId": resource_id},
            json=dict(request),
            timeout=self._apply_timeout,
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_APPLY_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data


__all__ = ["NoteCollabClient"]
