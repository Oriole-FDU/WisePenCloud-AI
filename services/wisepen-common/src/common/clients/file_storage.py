"""
wisepen-file-storage-service 的 Python 侧 typed facade
Java RemoteStorageService Feign 接口
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-file-storage-service"
_DEFAULT_DOWNLOAD_DURATION_SECONDS = 900


@dataclass
class UploadInitRequest:
    md5: str
    extension: str
    scene: str
    biz_path: str
    expected_size: int
    config_id: Optional[int] = None


@dataclass
class UploadInitResponse:
    flash_uploaded: bool
    domain: str
    object_key: str
    put_url: str = ""
    callback_header: str = ""


@dataclass
class StorageRecord:
    object_key: str
    md5: str = ""
    size: int = 0
    file_id: Optional[int] = None
    domain: str = ""


class FileStorageClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    @property
    def service_name(self) -> str:
        return self._service_name

    async def init_upload(self, req: UploadInitRequest) -> UploadInitResponse:
        data = await self._rpc.post(
            self._service_name,
            "/internal/storage/initUpload",
            json={
                "md5": req.md5,
                "extension": req.extension,
                "scene": req.scene,
                "bizPath": req.biz_path,
                "configId": req.config_id,
                "expectedSize": req.expected_size,
            },
        )
        if not isinstance(data, dict) or not data.get("objectKey"):
            raise RpcError(
                service_name=self._service_name,
                path="/internal/storage/initUpload",
                msg=f"unexpected data payload: {data!r}",
            )
        return UploadInitResponse(
            flash_uploaded=bool(data.get("flashUploaded")),
            domain=str(data.get("domain") or ""),
            object_key=str(data.get("objectKey") or ""),
            put_url=str(data.get("putUrl") or ""),
            callback_header=str(data.get("callbackHeader") or ""),
        )

    async def get_download_url(
        self,
        object_key: str,
        duration_seconds: int = _DEFAULT_DOWNLOAD_DURATION_SECONDS,
    ) -> str:
        data = await self._rpc.get(
            self._service_name,
            "/internal/storage/getDownloadUrl",
            params={"objectKey": object_key, "duration": duration_seconds},
        )
        if not isinstance(data, str) or not data:
            raise RpcError(
                service_name=self._service_name,
                path="/internal/storage/getDownloadUrl",
                msg=f"unexpected data payload: {data!r}",
            )
        return data

    async def get_file_record(self, object_key: str) -> Optional[StorageRecord]:
        data = await self._rpc.get(
            self._service_name,
            "/internal/storage/getFileRecord",
            params={"objectKey": object_key},
        )
        if data is None:
            return None
        if not isinstance(data, dict) or not data.get("objectKey"):
            raise RpcError(
                service_name=self._service_name,
                path="/internal/storage/getFileRecord",
                msg=f"unexpected data payload: {data!r}",
            )
        return StorageRecord(
            object_key=str(data.get("objectKey") or ""),
            md5=str(data.get("md5") or ""),
            size=int(data.get("size") or 0),
            file_id=int(data["fileId"]) if data.get("fileId") is not None else None,
            domain=str(data.get("domain") or ""),
        )

    async def delete_file(self, object_key: str) -> None:
        await self._rpc.post(
            self._service_name,
            "/internal/storage/deleteFiles",
            json=[object_key],
        )
