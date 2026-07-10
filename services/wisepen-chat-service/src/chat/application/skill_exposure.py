from __future__ import annotations

from typing import Optional, Set

from chat.domain.entities.skill import SkillMeta


def resolve_requested_ids(
    user_defined_ids: Optional[Set[str]],
    policy_ids: Optional[Set[str]],
) -> Set[str]:
    if user_defined_ids is not None:
        return set(user_defined_ids)
    return set(policy_ids or set())


def merge_skill_meta(*skill_groups: list[SkillMeta]) -> list[SkillMeta]:
    merged: list[SkillMeta] = []
    seen: set[str] = set()
    for skills in skill_groups:
        for skill in skills:
            if skill.skill_id in seen:
                continue
            seen.add(skill.skill_id)
            merged.append(skill)
    return merged
