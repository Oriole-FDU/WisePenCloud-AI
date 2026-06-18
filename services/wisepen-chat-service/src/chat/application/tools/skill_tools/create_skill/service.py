from __future__ import annotations

from chat.application.tools.skill_tools.create_skill.models import CreateSkillRequest
from chat.application.tools.skill_tools.create_skill.serializer import package_skill
from chat.application.tools.skill_tools.create_skill.skill_publisher import (
    SkillPublishResult,
    SkillPublisher,
)


class CreateSkillService:
    """Skill 创建编排入口。

    Service 负责：打包 zip → 调用 Publisher 发布 → 返回结果。
    不关心参数校验（由 Tool 层完成）和具体存储细节（由 Publisher 实现）。
    """

    def __init__(self, *, publisher: SkillPublisher) -> None:
        self._publisher = publisher

    async def create(
        self,
        request: CreateSkillRequest,
        *,
        user_id: str,
        session_id: str,
    ) -> SkillPublishResult:
        """打包并发布 Skill。

        Parameters
        ----------
        request : CreateSkillRequest
            已通过业务校验的创建请求。
        user_id : str
            从可信上下文获取的已鉴权用户 ID。
        session_id : str
            从可信上下文获取的会话 ID。
        """
        # 1. 打包为 zip（含 SKILL.md + references/ + scripts/ + assets/）
        package = package_skill(
            skill_id=request.skill_id,
            trigger_description=request.trigger_description,
            title=request.title,
            body=request.body,
            children=request.children,
            references=request.references,
            scripts=request.scripts,
            assets=request.assets,
            user_id=user_id,
            session_id=session_id,
        )

        # 2. 通过 Publisher 发布
        return await self._publisher.publish(
            skill_id=request.skill_id,
            title=request.title,
            trigger_description=request.trigger_description,
            package=package,
            user_id=user_id,
            session_id=session_id,
        )
