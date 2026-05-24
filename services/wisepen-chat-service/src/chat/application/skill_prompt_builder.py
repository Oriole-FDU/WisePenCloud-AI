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
