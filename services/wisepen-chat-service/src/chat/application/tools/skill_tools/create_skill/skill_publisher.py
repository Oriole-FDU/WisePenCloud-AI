from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SkillPublishResult:
    """Skill 发布结果。"""

    skill_id: str
    version: int
    published_at: datetime
    status: str  # "published" | "pending"


@runtime_checkable
class SkillPublisher(Protocol):
    """Skill 发布协议，实现委托给 Java ai-asset-service。"""

    async def publish(
        self,
        *,
        skill_id: str,
        title: str,
        trigger_description: str,
        package: bytes,
        user_id: str,
        session_id: str,
    ) -> SkillPublishResult:
        """发布 Skill 包。

        Parameters
        ----------
        package : bytes
            遵循 Agent Skills 规范的 zip 包，包含 SKILL.md + references/ + scripts/ + assets/。
        """
        ...
