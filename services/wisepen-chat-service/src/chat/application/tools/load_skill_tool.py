from typing import Any, Dict

from common.logger import log_error, log_fail

from chat.domain.interfaces.tool import BaseTool
from chat.domain.repositories import SkillRepository


class LoadSkillTool(BaseTool):
    """
    按 skill_id 懒加载 SKILL.md 正文 + assets manifest 摘要
    skill_id 必须在 tool_context['allowed_skill_ids']（本轮 matcher 命中的白名单）中，否则拒绝加载，防止 LLM 幻觉
    """

    def __init__(self, skill_repo: SkillRepository) -> None:
        self._skill_repo = skill_repo

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "Lazy-load the full SKILL.md content and assets manifest for a given skill. "
            "Only call this when the user's request is DIRECTLY covered by one of the Available Skills listed in the system context. "
            "Do NOT call speculatively. After loading, strictly follow the instructions in SKILL.md; "
            "call load_skill_asset to open a specific reference/template only if SKILL.md says you need it."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The slug id of the skill to load (e.g. 'paper-translation'). Must match one of the Available Skills.",
                },
            },
            "required": ["skill_id"],
        }

    @property
    def is_ephemeral_output(self) -> bool:
        # 本工具返回的消息无需持久化
        return True

    @property
    def reserved(self) -> bool:
        # 系统保留，不应被用户级 deny 屏蔽
        return True

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        skill_id = (kwargs.get("skill_id") or "").strip()
        if not skill_id:
            return "[Tool Error] Missing required argument: skill_id."

        allowed = set(context.get("allowed_skill_ids") or [])
        if skill_id not in allowed:
            log_fail(
                "load_skill 权限校验",
                "skill_id 不在本轮候选白名单",
                skill_id=skill_id,
                allowed=sorted(allowed),
            )
            return (
                f"[Tool Error] Skill '{skill_id}' is not available in this turn. "
                f"Allowed: {sorted(allowed) or 'none'}."
            )

        try:
            skill = await self._skill_repo.get(skill_id)
        except Exception as e:
            log_error("load_skill 查询", e, skill_id=skill_id)
            return f"[Tool Error] Failed to load skill '{skill_id}': {type(e).__name__}"

        if skill is None:
            return f"[Tool Error] Skill '{skill_id}' not found."

        # 拼接 header + SKILL.md + assets manifest 摘要
        lines = [
            f"[Loaded Skill] id={skill.skill_id} version={skill.version}",
            f"[Display Name] {skill.display_name}",
            "",
            "===== SKILL.md BEGIN =====",
            skill.skill_md.rstrip(),
            "===== SKILL.md END =====",
        ]

        if skill.assets_manifest:
            lines.append("")
            lines.append("[Assets Manifest] (use load_skill_asset to open any of these)")
            for asset in skill.assets_manifest:
                lines.append(
                    f"- path={asset.path} kind={asset.kind} size={asset.size_bytes} — {asset.description}"
                )

        return "\n".join(lines)
