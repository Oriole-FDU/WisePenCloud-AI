from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-file-storage-service"
_INIT_UPLOAD_URL_PATH = "/internal/storage/initUpload"
_GET_DOWNLOAD_URL_PATH = "/internal/storage/getDownloadUrl"


class UploadInitResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    flash_uploaded: bool = Field(alias="flashUploaded")
    domain: str = ""
    object_key: str = Field(alias="objectKey")
    put_url: str = Field(default="", alias="putUrl")
    callback_header: str = Field(default="", alias="callbackHeader")


class FileStorageClient:
    """Sandbox 侧的 File Storage typed facade，并负责预签名 URL 的字节传输。"""

    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
        transfer_timeout_seconds: float = 120.0,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def init_upload(
        self,
        *,
        md5: str,
        extension: str,
        scene: str,
        biz_tag: str,
        expected_size: int,
        config_id: int | None = None,
    ) -> UploadInitResponse:
        data = await self._rpc.post(
            self._service_name,
            _INIT_UPLOAD_URL_PATH,
            json={
                "md5": md5,
                "extension": extension,
                "scene": scene,
                "bizTag": biz_tag,
                "configId": config_id,
                "expectedSize": expected_size,
                "isNeedCallback": True,
            },
        )
        if not isinstance(data, dict) or not data.get("objectKey"):
            raise RpcError(
                service_name=self._service_name, path=_INIT_UPLOAD_URL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return UploadInitResponse.model_validate(data)

    async def get_download_url(
        self,
        object_key: str,
        *,
        duration_seconds: int,
        content_disposition: str | None = None,
    ) -> str:
        params: dict[str, str | int] = {
            "objectKey": object_key,
            "duration": duration_seconds,
        }
        if content_disposition:
            params["contentDisposition"] = content_disposition
        data = await self._rpc.get(
            self._service_name,
            _GET_DOWNLOAD_URL_PATH,
            params=params,
        )
        if not isinstance(data, str) or not data:
            raise RpcError(
                service_name=self._service_name, path=_GET_DOWNLOAD_URL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data