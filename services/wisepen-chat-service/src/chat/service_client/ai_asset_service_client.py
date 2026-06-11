from __future__ import annotations

from typing import List, Optional, Set

from chat.domain.entities import SkillMeta, Skill
from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-ai-asset-service"
_GET_SKILL_PATH = "/internal/skill/getSkillByResourceId"
_LIST_PUBLISHED_SKILLS_META_PATH = "/internal/skill/listPublishedSkillsMetaByResourceIds"


class AIAssetClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def list_published_skills_meta(self, skill_ids: Set[str]) -> List[SkillMeta]:
        payloads = await self._list_published_skills_meta_by_resource_ids(skill_ids)
        metas = [SkillMeta.from_response(item) for item in payloads]
        return [meta for meta in metas if meta.skill_id]

    async def get_skill_with_version(self, skill_id: str, skill_version: int) -> Optional[Skill]:
        published_skill_res = await self._get_skill_by_resource_id(skill_id, skill_version)
        return Skill.from_response(published_skill_res)

    async def get_published_skill(self, skill_id: str) -> Optional[Skill]:
        published_skill_res = await self._get_skill_by_resource_id(skill_id)
        return Skill.from_response(published_skill_res)

    async def _get_skill_by_resource_id(self, resource_id: str, skill_version: int = None) -> dict:
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
        return data

    async def _list_published_skills_meta_by_resource_ids(self, resource_ids: Set[str]) -> List[dict]:
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
        return data
