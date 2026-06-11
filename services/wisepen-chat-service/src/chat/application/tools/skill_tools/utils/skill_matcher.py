from abc import ABC, abstractmethod
from typing import List, Set, Optional

from common.logger import error

from chat.core.config.app_settings import settings
from chat.service_client import AIAssetClient
from chat.domain.entities.skill import SkillMeta


class SkillMatcher(ABC):
    """
    Skill 筛选器，返回当前请求可展示给 LLM 的 Skill 元信息
    """

    @abstractmethod
    async def match(
            self,
            on_demand_skill_ids: Set[str],
            user_query: str,
            skill_match_top_k: Optional[int] = None
    ) -> List[SkillMeta]: ...


class DefaultSkillMatcher(SkillMatcher):
    """
    默认 Skill 筛选器
    """

    def __init__(self, ai_asset_client: AIAssetClient) -> None:
        self._ai_asset_client = ai_asset_client

    async def match(
            self,
            on_demand_skill_ids: Set[str],
            user_query: str,
            skill_match_top_k: Optional[int] = None
    ) -> List[SkillMeta]:
        if not on_demand_skill_ids:
            return []

        skill_meta_list:List[SkillMeta] = []
        try:
            skill_meta_list = await self._ai_asset_client.list_published_skills_meta(on_demand_skill_ids)
        except Exception as e:
            error("skill metadata resolve failed.", count=len(on_demand_skill_ids), exc=e)

        top_k = max(1, skill_match_top_k or settings.SKILL_MATCH_TOP_K)
        return skill_meta_list[:top_k]

