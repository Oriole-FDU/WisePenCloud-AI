from abc import ABC, abstractmethod
from typing import List, Set, Optional

from common.logger import error

from chat.core.config.app_settings import settings
from chat.domain.entities.skill import SkillMeta
from chat.service_client import AIAssetClient
from chat.application.tools.skill_tools.utils.builtin_skills import get_builtin_skill_meta, is_builtin_skill_id


ALWAYS_AVAILABLE_BUILTIN_SKILL_IDS = {"builtin:skill-creator"}


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
        requested_skill_ids = set(on_demand_skill_ids or set())
        # 系统级内置 Skill 始终进入候选集，但不占用显式请求 Skill 的 top-k 名额。
        always_builtin_skill_ids = ALWAYS_AVAILABLE_BUILTIN_SKILL_IDS - requested_skill_ids

        requested_builtin_skill_ids = {
            skill_id for skill_id in requested_skill_ids if is_builtin_skill_id(skill_id)
        }
        external_skill_ids = requested_skill_ids - requested_builtin_skill_ids

        requested_skill_meta_list: List[SkillMeta] = []
        for skill_id in sorted(requested_builtin_skill_ids): # 处理显式请求的内置 Skill
            meta = get_builtin_skill_meta(skill_id)
            if meta is not None:
                requested_skill_meta_list.append(meta)

        # 处理外置 Skill
        if external_skill_ids:
            try:
                requested_skill_meta_list.extend(await self._ai_asset_client.list_published_skills_meta(external_skill_ids))
            except Exception as e:
                error("skill metadata resolve failed.", count=len(external_skill_ids), exc=e)

        top_k = max(1, skill_match_top_k or settings.SKILL_MATCH_TOP_K)
        visible_skill_meta_list = requested_skill_meta_list[:top_k]
        seen_skill_ids = {skill.skill_id for skill in visible_skill_meta_list}

        for skill_id in sorted(always_builtin_skill_ids):
            if skill_id in seen_skill_ids:
                continue
            meta = get_builtin_skill_meta(skill_id)
            if meta is not None:
                visible_skill_meta_list.append(meta)
                seen_skill_ids.add(skill_id)

        return visible_skill_meta_list

