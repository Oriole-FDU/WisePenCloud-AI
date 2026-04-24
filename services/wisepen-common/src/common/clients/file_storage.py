"""
wisepen-file-storage-service 的 Python 侧 typed facade
Java RemoteStorageService Feign 接口
"""
from __future__ import annotations

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-file-storage-service"
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
