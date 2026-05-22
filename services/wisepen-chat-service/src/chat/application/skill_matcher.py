from abc import ABC, abstractmethod
from typing import List

from common.logger import log_error, log_fail, log_event

from chat.core.config.app_settings import settings
from chat.domain.entities.skill import SkillMeta
from chat.domain.repositories import SkillRepository


class SkillMatcher(ABC):
    """
    Skill 可用清单接口：返回当前会话可展示给 LLM 的 Skill 元信息。

    当前策略不再用字符串匹配预筛，而是把轻量 metadata 交给 LLM 判断是否加载。
    接口名暂时保持 match 以减少调用方改动。
    """

    @abstractmethod
    async def warmup(self) -> None: ...

    @abstractmethod
    def match(self, query: str) -> List[SkillMeta]: ...


class KeywordSkillMatcher(SkillMatcher):
    """
    可用 Skill metadata 缓存。

    历史类名暂时保留以兼容容器装配；行为已经不是关键词匹配。
    match(query) 会忽略 query，返回所有 enabled Skill 的轻量信息，让 LLM 自行决定是否调用 load_skill。
    """

    def __init__(self, skill_repo: SkillRepository) -> None:
        self._skill_repo = skill_repo
        self._cache: List[SkillMeta] = []
        self._warmed: bool = False

    async def warmup(self) -> None:
        try:
            metas = await self._skill_repo.list_enabled_skill_metas()
        except Exception as e:
            # 捕获所有异常，保证服务可启动 / 周期刷新不炸
            # 失败时不擦除 self._cache，已有 last-good 继续服务，防止被 Mongo 抖动打回"无 Skill 能力"
            log_error("Skill metadata warmup", e, had_cache=bool(self._cache))
            self._warmed = True
            return

        self._cache = sorted(metas, key=lambda m: m.skill_id)
        self._warmed = True
        log_event("Skill metadata warmup 完成", count=len(metas))

    def match(self, query: str) -> List[SkillMeta]:
        if not self._cache:
            log_fail(
                "Skill metadata",
                "cache 为空，本次返回空列表",
            )
            return []

        max_count = max(1, settings.SKILL_DISCOVERY_MAX_COUNT)
        return self._cache[:max_count]
