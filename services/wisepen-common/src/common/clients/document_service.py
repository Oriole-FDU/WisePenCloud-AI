from __future__ import annotations

from dataclasses import dataclass

from common.core.constants import SecurityConstants
from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-document-service"


@dataclass
class DocumentUploadInitRequest:
    filename: str
    extension: str
    md5: str
    size: int


@dataclass
class DocumentUploadInitResponse:
    document_id: str
    put_url: str = ""
    callback_header: str = ""
    object_key: str = ""
    flash_uploaded: bool = False


@dataclass
class DocumentInfo:
    document_id: str
    status: int | str | None = None
    source_object_key: str = ""
    preview_object_key: str = ""
    text_mongo_id: str = ""
    error_message: str = ""


class DocumentServiceClient:
    """wisepen-document-service typed facade"""

    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def init_upload(
        self,
        req: DocumentUploadInitRequest,
        user_id: str,
    ) -> DocumentUploadInitResponse:
        data = await self._rpc.post(
            self._service_name,
            "/document/upload/init",
            json={
                "filename": req.filename,
                "extension": req.extension,
                "md5": req.md5,
                "size": req.size,
            },
            headers={SecurityConstants.HEADER_USER_ID: str(user_id)},
        )
        if not isinstance(data, dict) or not data.get("documentId"):
            raise RpcError(
                service_name=self._service_name,
                path="/document/upload/init",
                msg=f"unexpected data payload: {data!r}",
            )
        return DocumentUploadInitResponse(
            document_id=str(data.get("documentId") or ""),
            put_url=str(data.get("putUrl") or ""),
            callback_header=str(data.get("callbackHeader") or ""),
            object_key=str(data.get("objectKey") or ""),
            flash_uploaded=bool(data.get("flashUploaded")),
        )

    async def get_document_info(self, document_id: str) -> DocumentInfo:
        data = await self._rpc.get(
            self._service_name,
            f"/remote/document/info/{document_id}",
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=f"/remote/document/info/{document_id}",
                msg=f"unexpected data payload: {data!r}",
            )
        status = data.get("status")
        if isinstance(status, dict):
            status = status.get("code")
        return DocumentInfo(
            document_id=str(data.get("documentId") or document_id),
            status=status,
            source_object_key=str(data.get("sourceObjectKey") or ""),
            preview_object_key=str(data.get("previewObjectKey") or ""),
            text_mongo_id=str(data.get("textMongoId") or ""),
            error_message=str(data.get("errorMessage") or ""),
        )

    async def delete_document(self, document_id: str, user_id: str) -> None:
        await self._rpc.delete(
            self._service_name,
            f"/document/{document_id}",
            headers={SecurityConstants.HEADER_USER_ID: str(user_id)},
        )
