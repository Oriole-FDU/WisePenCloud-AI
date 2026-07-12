from __future__ import annotations

from typing import Any, List, Mapping, Optional, Set

import httpx

from chat.domain.entities import SkillMeta, Skill
from chat.domain.entities.skill import SkillInfo, SkillAssetUploadInitResult, SkillAssetUploadInitAsset
from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-ai-asset-service"
_GET_SKILL_PATH = "/internal/skill/getSkillByResourceId"
_LIST_PUBLISHED_SKILLS_META_PATH = "/internal/skill/listPublishedSkillsMetaByResourceIds"
_CREATE_SKILL_PATH = "/skill/createSkill"
_CHANGE_SKILL_INFO_PATH = "/skill/changeSkillInfo"
_GET_SKILL_INFO_PATH = "/skill/getSkillInfo"
_INIT_UPLOAD_SKILL_ASSETS_PATH = "/skill/initUploadSkillAssets"
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

    async def list_published_skills_meta(self, skill_ids: Set[str]) -> List[SkillMeta]:
        metas = await self._list_published_skills_meta_by_resource_ids(skill_ids)
        return [meta for meta in metas if meta.skill_id]

    async def get_skill_with_version(self, skill_id: str, skill_version: int) -> Optional[Skill]:
        return await self._get_skill_by_resource_id(skill_id, skill_version)

    async def get_published_skill(self, skill_id: str) -> Optional[Skill]:
        return await self._get_skill_by_resource_id(skill_id)

    async def _get_skill_by_resource_id(self, resource_id: str, skill_version: int = None) -> Optional[Skill]:
        try:
            data = await self._rpc.get(
                self._service_name,
                _GET_SKILL_PATH,
                params={"resourceId": resource_id, "skillVersion": skill_version},
            )
        except RpcError as e:
            raise e
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name, path=_GET_SKILL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return Skill.from_response(data)

    async def _list_published_skills_meta_by_resource_ids(self, resource_ids: Set[str]) -> List[SkillMeta]:
        try:
            data = await self._rpc.post(
                self._service_name,
                _LIST_PUBLISHED_SKILLS_META_PATH,
                json={"resourceIds": sorted(resource_ids)},
            )
        except RpcError as e:
            raise e
        if not isinstance(data, list):
            raise RpcError(
                service_name=self._service_name, path=_LIST_PUBLISHED_SKILLS_META_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return [SkillMeta.from_response(item) for item in data]


    async def create_skill_by_agent(self, title: str, name: str, description: str) -> str:
        try:
            data = await self._rpc.post(
                self._service_name,
                _CREATE_SKILL_PATH,
                json={"title": title, "name": name, "description": description, "sourceType": "BY_AGENT"},
            )
        except RpcError as e:
            raise e
        if not isinstance(data, str) or not data.strip():
            raise RpcError(
                service_name=self._service_name,
                path=_CREATE_SKILL_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data.strip()

    async def update_skill_info(self, resource_id: str, name: str, description: str) -> None:
        try:
            await self._rpc.post(
                self._service_name,
                _CHANGE_SKILL_INFO_PATH,
                json={"resourceId": resource_id, "name": name, "description": description},
            )
        except RpcError as e:
            raise e

    async def get_skill_info(self, resource_id: str) -> Optional[SkillInfo]:
        try:
            data = await self._rpc.post(
                self._service_name,
                _GET_SKILL_INFO_PATH,
                params={"resourceId": resource_id},
            )
        except RpcError as e:
            raise e
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_SKILL_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return SkillInfo.from_response(data)

    async def init_upload_skill_assets(self, resource_id: str, draft_version: int, assets: list[SkillAssetUploadInitAsset]) -> Optional[SkillAssetUploadInitResult]:
        try:
            data = await self._rpc.post(
                self._service_name,
                _INIT_UPLOAD_SKILL_ASSETS_PATH,
                json={"resourceId": resource_id, "draftVersion": draft_version, "assets": [asset.to_request() for asset in assets]},
            )
        except RpcError as e:
            raise e
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_INIT_UPLOAD_SKILL_ASSETS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return SkillAssetUploadInitResult.from_response(data)

    async def upload_skill_asset_content(self, put_url: str, content: bytes, *, callback_header: str | None = None) -> None:
        headers = {"Content-Type": "application/octet-stream"}
        if callback_header:
            headers[_OSS_CALLBACK_HEADER] = callback_header
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._upload_timeout)) as client:
            response = await client.put(put_url, content=content, headers=headers)
            response.raise_for_status()
