from abc import ABC, abstractmethod
from typing import List, Optional

from chat.domain.entities.skill import Skill, SkillMeta


class SkillMetadataRepository(ABC):
    """
    已发布 Skill 的只读 metadata 仓储接口（AI 服务侧 view）
    本服务仅消费已发布 Skill。因此本接口无 upsert / delete / set_enabled 等方法
    """

    @abstractmethod
    async def list_enabled_meta(self) -> List[SkillMeta]:
        """
        返回所有 enabled=True 的 Skill 轻量元信息（不含 skill_md 与 assets_manifest 正文字段）
        """
        ...

    @abstractmethod
    async def get(self, skill_id: str) -> Optional[Skill]:
        """
        按 skill_id 读取完整文档（含 skill_md + assets_manifest）
        """
        ...
