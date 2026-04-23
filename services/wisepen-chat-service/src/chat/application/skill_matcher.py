from abc import ABC, abstractmethod
from typing import List

from common.logger import log_error, log_fail, log_event

from chat.core.config.app_settings import settings
from chat.domain.entities.skill import SkillMeta
from chat.domain.repositories import SkillRepository


class SkillMatcher(ABC):
    """
    Skill 预筛选接口：根据用户 query 返回可能相关的 Skill 元信息 shortlist。

    实现可以换成 embedding / 语义相似度，接口保持不变。
    """

    @abstractmethod
    async def warmup(self) -> None: ...

    @abstractmethod
    def match(self, query: str) -> List[SkillMeta]: ...


class KeywordSkillMatcher(SkillMatcher):
    """
    最简关键词预筛：大小写无关 substring 匹配 triggers，按命中数排序取 top_k。
    """

    def __init__(self, skill_repo: SkillRepository) -> None:
        self._skill_repo = skill_repo
        self._cache: List[SkillMeta] = []
        self._warmed: bool = False

    async def warmup(self) -> None:
        try:
            metas = await self._skill_repo.list_enabled_meta()
        except Exception as e:
            # 捕获所有异常，保证服务可启动 / 周期刷新不炸
            # 失败时不擦除 self._cache，已有 last-good 继续服务，防止被 Mongo 抖动打回"无 Skill 能力"
            log_error("Skill matcher warmup", e, had_cache=bool(self._cache))
            self._warmed = True
            return

        self._cache = metas
        self._warmed = True
        log_event("Skill matcher warmup 完成", count=len(metas))

    def match(self, query: str) -> List[SkillMeta]:
        if not self._cache:
            log_fail(
                "Skill matcher",
                "cache 为空，本次 match 返回空列表",
            )
            return []

        if not query:
            return []

        lowered = query.lower()
        scored: List[tuple[int, SkillMeta]] = []
        for meta in self._cache:
            # 大小写无关 substring：同一 trigger 命中只记 1 分（去重）；命中数越多排名越高
            hits = 0
            for trig in meta.triggers:
                if trig and trig.lower() in lowered:
                    hits += 1
            if hits > 0:
                scored.append((hits, meta))

        if not scored:
            return []

        # 先按命中数降序；命中数相同时按 skill_id 字典序稳定
        scored.sort(key=lambda x: (-x[0], x[1].skill_id))
        top_k = max(1, settings.SKILL_MATCH_TOP_K)
        return [m for _, m in scored[:top_k]]
