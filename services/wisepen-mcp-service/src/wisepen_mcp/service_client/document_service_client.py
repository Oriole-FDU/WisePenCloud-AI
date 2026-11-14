from __future__ import annotations

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from wisepen_mcp.domain.entities import DocumentSearchText


_DOCUMENT_SERVICE_NAME = "wisepen-document-service"
_GET_DOCUMENT_SEARCH_TEXT_PATH = "/internal/document/getDocumentSearchText"
_DOWNLOAD_PATH = "/document/download"


class DocumentClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DOCUMENT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def get_search_text(self, resource_id: str) -> DocumentSearchText:
        data = await self._rpc.get(
            self._service_name,
            _GET_DOCUMENT_SEARCH_TEXT_PATH,
            params={"resourceId": resource_id},
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_DOCUMENT_SEARCH_TEXT_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return DocumentSearchText.model_validate(data)

    async def get_source_file_download_url(self, *, resource_id: str) -> str:
        response = await self._rpc.request_raw(
            "GET",
            self._service_name,
            _DOWNLOAD_PATH,
            params={"resourceId": resource_id, "downloadType": "ORIGINAL"},
        )
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            if location:
                return location
            raise RpcError(
                service_name=self._service_name,
                path=_DOWNLOAD_PATH,
                status=response.status_code,
                msg="redirect response is missing the Location header.",
            )
        code: int | None = None
        msg = response.text[:500]
        try:
            body = response.json()
            if isinstance(body, dict):
                msg = str(body.get("msg") or msg)
                code = int(body["code"]) if "code" in body else None
        except Exception:
            pass
        raise RpcError(
            service_name=self._service_name,
            path=_DOWNLOAD_PATH,
            status=response.status_code,
            code=code,
            msg=msg,
        )


__all__ = ["DocumentClient"]
