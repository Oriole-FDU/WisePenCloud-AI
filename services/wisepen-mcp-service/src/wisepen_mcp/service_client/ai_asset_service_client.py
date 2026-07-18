from __future__ import annotations

import httpx

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from wisepen_mcp.domain.entities import (
    SkillAssetUploadInitAsset,
    SkillAssetUploadInitResult,
    SkillInfo,
)


_DEFAULT_SERVICE_NAME = "wisepen-ai-asset-service"
_CREATE_SKILL_PATH = "/skill/createSkill"
_CHANGE_SKILL_INFO_PATH = "/skill/changeSkillInfo"
_GET_SKILL_INFO_PATH = "/skill/getSkillInfo"
_INIT_UPLOAD_SKILL_ASSETS_PATH = "/skill/initUploadSkillAssets"
_OSS_UPLOAD_PATH = "/skillAssetUpload"
_OSS_CALLBACK_HEADER = "x-oss-callback"


class AIAssetClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
        upload_timeout: float = 30.0,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name
        self._upload_timeout = upload_timeout

    async def create_skill_by_agent(self, title: str, name: str, description: str) -> str:
        data = await self._rpc.post(
            self._service_name,
            _CREATE_SKILL_PATH,
            json={"title": title, "name": name, "description": description, "sourceType": "BY_AGENT"},
        )
        if not isinstance(data, str) or not data.strip():
            raise RpcError(
                service_name=self._service_name,
                path=_CREATE_SKILL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data.strip()

    async def update_skill_info(self, resource_id: str, name: str, description: str) -> None:
        await self._rpc.post(
            self._service_name,
            _CHANGE_SKILL_INFO_PATH,
            json={"resourceId": resource_id, "name": name, "description": description},
        )

    async def get_skill_info(self, resource_id: str) -> SkillInfo:
        data = await self._rpc.post(
            self._service_name,
            _GET_SKILL_INFO_PATH,
            params={"resourceId": resource_id},
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_SKILL_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        try:
            return SkillInfo.from_response(data)
        except Exception as e:
            raise RpcError(
                service_name=self._service_name,
                path=_GET_SKILL_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
                cause=e,
            ) from e

    async def init_upload_skill_assets(
        self,
        resource_id: str,
        draft_version: int,
        assets: list[SkillAssetUploadInitAsset],
    ) -> SkillAssetUploadInitResult:
        data = await self._rpc.post(
            self._service_name,
            _INIT_UPLOAD_SKILL_ASSETS_PATH,
            json={
                "resourceId": resource_id,
                "draftVersion": draft_version,
                "assets": [asset.to_request() for asset in assets],
            },
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_INIT_UPLOAD_SKILL_ASSETS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        try:
            return SkillAssetUploadInitResult.from_response(data)
        except Exception as e:
            raise RpcError(
                service_name=self._service_name,
                path=_INIT_UPLOAD_SKILL_ASSETS_PATH,
                msg=f"unexpected data payload: {data!r}",
                cause=e,
            ) from e

    async def upload_skill_asset_content(
        self,
        put_url: str,
        content: bytes,
        *,
        callback_header: str | None = None,
    ) -> None:
        headers: dict[str, str] = {"Content-Type": "application/octet-stream"}
        if callback_header:
            headers[_OSS_CALLBACK_HEADER] = callback_header

        async with httpx.AsyncClient(timeout=httpx.Timeout(self._upload_timeout)) as client:
            response = await client.put(put_url, content=content, headers=headers)

        if callback_header and response.status_code != 200:
            raise RpcError(
                service_name="oss",
                path=_OSS_UPLOAD_PATH,
                status=response.status_code,
                msg=_format_upload_error(response, "OSS callback upload failed"),
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RpcError(
                service_name="oss",
                path=_OSS_UPLOAD_PATH,
                status=response.status_code,
                msg=_format_upload_error(response, "OSS upload failed"),
            )


def _format_upload_error(response: httpx.Response, prefix: str) -> str:
    body = response.text[:500]
    return f"{prefix}: status={response.status_code} body={body!r}"
