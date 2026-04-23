from typing import List, Optional

from chat.domain.entities.skill import Skill, SkillMeta
from chat.domain.repositories import SkillRepository


class MongoSkillRepository(SkillRepository):
    """
    SkillRepository 的 MongoDB (Beanie) 只读实现。

    Mongo 里的 wisepen_published_skill collection 由 Java wisepen-skill-service 写；
    本类只做 read view，不提供任何写入方法。
    """

    async def list_enabled_meta(self) -> List[SkillMeta]:
        # enabled 条目数通常不多，这里简化：读完再映射 dataclass。
        # 如果未来量级上来，可以引入 Beanie 的 projection 模型节流。
        docs = await Skill.find(Skill.enabled == True).to_list()  # noqa: E712
        return [
            SkillMeta(
                skill_id=doc.skill_id,
                display_name=doc.display_name,
                description=doc.description,
                triggers=list(doc.triggers),
                version=doc.version,
            )
            for doc in docs
        ]

    async def get(self, skill_id: str) -> Optional[Skill]:
        return await Skill.find_one(Skill.skill_id == skill_id)
