from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DOCUMENT_SERVICE_NAME = "wisepen-document-service"
_DOCUMENT_UPLOAD_PATH = "/document/uploadDoc"
_WEB_APP_SERVICE_NAME = "wisepen-web-app-service"
_WEB_APP_UPLOAD_PATH = "/singleHtmlPage/uploadSingleHtmlPage"


class DocumentUploadTicket(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(min_length=1, alias="documentId")
    object_key: str = Field(min_length=1, alias="objectKey")
    put_url: str | None = Field(default=None, alias="putUrl")
    callback_header: str | None = Field(default=None, alias="callbackHeader")
    flash_uploaded: bool = Field(alias="flashUploaded")


class WebAppUploadTicket(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    single_html_page_id: str = Field(min_length=1, alias="singleHtmlPageId")
    version: int = Field(ge=1)
    object_key: str = Field(min_length=1, alias="objectKey")
    put_url: str | None = Field(default=None, alias="putUrl")
    callback_header: str | None = Field(default=None, alias="callbackHeader")
    flash_uploaded: bool = Field(alias="flashUploaded")


class DocumentServiceClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DOCUMENT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def init_upload(
        self,
        *,
        filename: str,
        extension: str,
        md5: str,
        expected_size: int,
    ) -> DocumentUploadTicket:
        data = await self._rpc.post(
            self._service_name,
            _DOCUMENT_UPLOAD_PATH,
            json={
                "resourceId": None,
                "filename": filename,
                "extension": extension,
                "md5": md5,
                "expectedSize": expected_size,
            },
        )
        try:
            return DocumentUploadTicket.model_validate(data)
        except ValidationError as exc:
            raise RpcError(
                service_name=self._service_name,
                path=_DOCUMENT_UPLOAD_PATH,
                msg=f"unexpected data payload: {data!r}",
                cause=exc,
            ) from exc


class WebAppServiceClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _WEB_APP_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def init_upload(
        self,
        *,
        filename: str,
        md5: str,
        expected_size: int,
    ) -> WebAppUploadTicket:
        data = await self._rpc.post(
            self._service_name,
            _WEB_APP_UPLOAD_PATH,
            json={
                "resourceId": None,
                "filename": filename,
                "md5": md5,
                "expectedSize": expected_size,
            },
        )
        try:
            return WebAppUploadTicket.model_validate(data)
        except ValidationError as exc:
            raise RpcError(
                service_name=self._service_name,
                path=_WEB_APP_UPLOAD_PATH,
                msg=f"unexpected data payload: {data!r}",
                cause=exc,
            ) from exc


__all__ = [
    "DocumentServiceClient",
    "DocumentUploadTicket",
    "WebAppServiceClient",
    "WebAppUploadTicket",
]
