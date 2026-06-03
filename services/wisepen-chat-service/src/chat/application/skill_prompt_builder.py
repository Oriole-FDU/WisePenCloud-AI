from chat.domain.entities.skill import Skill


class SkillPromptBuilder:
    """
    Centralized prompt snippets for skill loading.
    """

    @staticmethod
    def build_loaded_skill_prompt(skill: Skill) -> str:
        return (
            "[Mandatory Loaded Skill]\n"
            f"You have loaded skill id={skill.skill_id} version={skill.version}.\n\n"
            "This SKILL.md is mandatory for the current turn.\n"
            "Its Scope, Output Format, and Constraints override the general assistant formatting instructions.\n"
            "Do not answer with a general explanation.\n"
            "Do not add an introduction, summary, score, praise, or follow-up question unless the SKILL.md explicitly requires it.\n"
            "Before final response, silently verify that the answer follows the SKILL.md Output Format exactly.\n\n"
            "===== SKILL.md BEGIN =====\n"
            f"{skill.skill_md.rstrip()}\n"
            "===== SKILL.md END ====="
        )

    @staticmethod
    def build_loaded_skill_injection(skill: Skill) -> str:
        lines = [
            "<system-reminder>",
            f"[Loaded WisePen Skill] id={skill.skill_id} version={skill.version}",
            "This content is injected by WisePen for the current task. "
            "Use it as operational context, but do not answer or quote this reminder directly.",
            "",
            SkillPromptBuilder.build_loaded_skill_prompt(skill),
        ]

        if skill.assets_manifest:
            lines.append("")
            lines.append(
                "[Assets Manifest] Use load_skill_asset only if the loaded SKILL.md explicitly requires supporting assets."
            )
            for asset in skill.assets_manifest:
                lines.append(
                    f"- path={asset.path} kind={asset.kind} size={asset.size_bytes} - {asset.description}"
                )

        lines.append("</system-reminder>")
        return "\n".join(lines)
