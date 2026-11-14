from typing import Any


def build_skill_output_placeholder(tool_call_arguments: dict[str, Any], output: Any) -> str:
    skill_id = tool_call_arguments.get("skill_id") or "unknown"
    return (
        "[Skill content omitted from persistent history. "
        f"skill_id={skill_id}. "
        "If this skill is needed again after summarization or in a later turn, call load_skill with this skill_id.]"
    )


def build_skill_asset_output_placeholder(tool_call_arguments: dict[str, Any], output: Any) -> str:
    skill_id = tool_call_arguments.get("skill_id") or "unknown"
    path = tool_call_arguments.get("path") or "unknown"
    return (
        "[Skill asset content omitted from persistent history. "
        f"skill_id={skill_id} path={path}. "
        "If this asset is needed again after summarization or in a later turn, reload the skill and call load_skill_asset.]"
    )
