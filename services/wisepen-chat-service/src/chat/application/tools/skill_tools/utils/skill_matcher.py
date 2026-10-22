from abc import ABC, abstractmethod
from typing import List, Set, Optional

from common.logger import error

from chat.core.config.app_settings import settings
from chat.domain.entities.skill import SkillMeta
from chat.service_client import AIAssetClient
from chat.application.tools.skill_tools.utils.builtin_skills import get_builtin_skill_meta, is_builtin_skill_id


ALWAYS_AVAILABLE_BUILTIN_SKILL_IDS = {
    "builtin:note-editor",
    "builtin:skill-creator",
}


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
        # 系统级内置 Skill 始终进入候选集，不受前端 on-demand 覆盖影响
        on_demand_skill_ids = set(on_demand_skill_ids or set()) | ALWAYS_AVAILABLE_BUILTIN_SKILL_IDS

        builtin_skill_ids = {skill_id for skill_id in on_demand_skill_ids if is_builtin_skill_id(skill_id)}
        external_skill_ids = on_demand_skill_ids - builtin_skill_ids

        skill_meta_list: List[SkillMeta] = []
        for skill_id in sorted(builtin_skill_ids): # 处理内置 Skill
            meta = get_builtin_skill_meta(skill_id)
            if meta is not None:
                skill_meta_list.append(meta)

        # 处理外置 Skill
        if external_skill_ids:
            try:
                skill_meta_list.extend(await self._ai_asset_client.list_published_skills_meta(external_skill_ids))
            except Exception as e:
                error("skill metadata resolve failed.", count=len(external_skill_ids), exc=e)

        top_k = max(1, skill_match_top_k or settings.SKILL_MATCH_TOP_K)
        return skill_meta_list[:top_k]

