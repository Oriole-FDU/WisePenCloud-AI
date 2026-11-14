"""
wisepen-file-storage-service 的 Python 侧 typed facade
Java RemoteStorageService Feign 接口
"""
from __future__ import annotations

from typing import Optional

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from chat.domain.entities import StorageRecord, UploadInitResponse


_DEFAULT_SERVICE_NAME = "wisepen-file-storage-service"
_INIT_UPLOAD_URL_PATH = "/internal/storage/initUpload"
_GET_DOWNLOAD_URL_PATH = "/internal/storage/getDownloadUrl"
_GET_FILE_RECORD_URL_PATH = "/internal/storage/getFileRecord"
_DELETE_FILE_URL_PATH = "/internal/storage/deleteFiles"
_FILE_RECORD_NOT_FOUND_CODE = 7111
_DEFAULT_DOWNLOAD_DURATION_SECONDS = 900


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

    async def init_upload(
        self,
        *,
        md5: str,
        extension: str,
        scene: str,
        biz_tag: str,
        provider_code: Optional[str],
        expected_size: int,
    ) -> UploadInitResponse:
        data = await self._rpc.post(
            self._service_name,
            _INIT_UPLOAD_URL_PATH,
            json={
                "md5": md5,
                "extension": extension,
                "scene": scene,
                "bizTag": biz_tag,
                "providerCode": provider_code,
                "expectedSize": expected_size,
            },
        )
        if not isinstance(data, dict) or not data.get("objectKey"):
            raise RpcError(
                service_name=self._service_name, path=_INIT_UPLOAD_URL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return UploadInitResponse.from_response(data)

    async def get_download_url(
        self,
        object_key: str,
        duration_seconds: int = _DEFAULT_DOWNLOAD_DURATION_SECONDS,
    ) -> str:
        data = await self._rpc.get(
            self._service_name,
            _GET_DOWNLOAD_URL_PATH,
            params={"objectKey": object_key, "duration": duration_seconds},
        )
        if not isinstance(data, str) or not data:
            raise RpcError(
                service_name=self._service_name, path=_GET_DOWNLOAD_URL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data

    async def get_file_record(self, object_key: str) -> Optional[StorageRecord]:
        data = await self._rpc.get(
            self._service_name,
            _GET_FILE_RECORD_URL_PATH,
            params={"objectKey": object_key},
        )
        if data is None:
            return None
        if not isinstance(data, dict) or not data.get("objectKey"):
            raise RpcError(
                service_name=self._service_name, path=_GET_FILE_RECORD_URL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return StorageRecord.from_response(data)

    # 删除文件接口
    async def delete_file(self, object_key: str) -> None:
        try:
            await self._rpc.delete(
                self._service_name,
                _DELETE_FILE_URL_PATH,
                json=[object_key],
            )
        except RpcError as exc:
            # 删除具有幂等语义：物理记录不存在时，目标状态已经达成。
            if exc.code != _FILE_RECORD_NOT_FOUND_CODE:
                raise
