from __future__ import annotations

from typing import Optional, Set

from chat.domain.entities.skill import SkillMeta


_LEGACY_SKILL_ID_ALIASES = {
    "wisepen-note-ai-diff": "builtin:wisepen-note-ai-diff",
}


def normalize_skill_id(skill_id: str) -> str:
    normalized = (skill_id or "").strip()
    return _LEGACY_SKILL_ID_ALIASES.get(normalized, normalized)


def resolve_requested_ids(
    user_defined_ids: Optional[Set[str]],
    policy_ids: Optional[Set[str]],
) -> Set[str]:
    if user_defined_ids is not None:
        return {normalize_skill_id(skill_id) for skill_id in user_defined_ids if skill_id}
    return {normalize_skill_id(skill_id) for skill_id in (policy_ids or set()) if skill_id}


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
