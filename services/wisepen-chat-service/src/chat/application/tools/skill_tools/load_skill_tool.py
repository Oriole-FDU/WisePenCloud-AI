from typing import Any, Dict

from chat.domain.entities import Skill
from chat.domain.interfaces import FileLoader
from chat.service_client import AIAssetClient
from chat.service_client.resource_service_client import ResourceClient

from chat.core.config.app_settings import settings
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.skill_tools.common import AllowedSkillIdCheck, build_skill_output_placeholder, SkillPermissionCheck
from chat.application.tools.skill_tools.utils.builtin_skills import get_builtin_skill, is_builtin_skill_id, read_builtin_skill_asset


class LoadSkillTool:
    """
    按 skill_id 懒加载 SKILL.md 正文 + assets manifest 摘要
    skill_id 必须在 tool_context['allowed_skill_ids']（本轮 matcher 命中的白名单）中，否则拒绝加载，防止 LLM 幻觉
    """

    def __init__(
        self,
        file_loader: FileLoader,
        ai_asset_client: AIAssetClient,
        resource_client: ResourceClient,
    ) -> None:
        self._file_loader = file_loader
        self._ai_asset_client = ai_asset_client
        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The id of the skill to load. Must match one of the Available Skills.",
                },
            },
            "required": ["skill_id"],
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="load_skill",
                description=(
                    "Lazy-load the full SKILL.md content and assets manifest for a given skill. "
                    "Only call this when the user's request is DIRECTLY covered by one of the Available Skills listed in the system context. "
                    "After loading, strictly follow the instructions in SKILL.md; "
                    "call load_skill_asset to open a specific reference/template only if SKILL.md says you need it."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=False,
                persisted_output_placeholder_factory=build_skill_output_placeholder,
                risk_level=ToolRiskLevel.MEDIUM,
                required_context_keys=("allowed_skill_ids",),
                timeout_seconds=8.0,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
            ),
            preflight_hooks=(AllowedSkillIdCheck(), SkillPermissionCheck(resource_client)),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        skill_id = (kwargs.get("skill_id") or "").strip()

        # 先检查是否为内置Skill
        if is_builtin_skill_id(skill_id):
            skill = get_builtin_skill(skill_id)
        else:
            skill = await self._ai_asset_client.get_published_skill(skill_id)
        if skill is None:
            raise ToolExecutionError(
                reason="Skill Not Found",
                detail_reason=f"Skill '{skill_id}' not found.",
                metadata={"skill_id": skill_id},
            )

        skill_md = await self._load_skill_md(skill)

        lines = [
            f"[Loaded Skill] skill_id={skill.skill_id} version={skill.version} name= {skill.name}",
            "<skill>",
            skill_md.rstrip(),
            "</skill>",
        ]

        if skill.assets_manifest:
            lines.append("")
            lines.append("<assets_manifest>")
            for asset in skill.assets_manifest:
                if asset.path == "/SKILL.md": continue
                lines.append(
                    f"-(path={asset.path} kind={asset.kind} size={asset.size_bytes}): {asset.description}"
                )
            lines.append("</assets_manifest>")
            lines.append("Use `load_skill_asset` to open any of assets manifests.")

        return "\n".join(lines)

    async def _load_skill_md(self, skill:Skill) -> str:
        skill_md_asset = next((asset for asset in skill.assets_manifest if asset.path == "/SKILL.md"), None)

        if not skill_md_asset:
            raise ToolExecutionError(
                reason="Skill.md Not Available",
                detail_reason=f"Failed to find Skill.md of corrupted skill '{skill.skill_id}'.",
                metadata={"skill_id": skill.skill_id},
            )

        if is_builtin_skill_id(skill.skill_id):
            # 内置 Skill 使用 read_builtin_skill_asset 加载
            try:
                raw = read_builtin_skill_asset(skill.skill_id, "/SKILL.md")
            except Exception as e:
                raise ToolExecutionError(
                    reason="Skill.md Load Failed",
                    detail_reason=f"Failed to load builtin asset: {type(e).__name__}",
                    retryable=False,
                    metadata={"skill_id": skill.skill_id, "path": "/SKILL.md", "detail": str(e)},
                )
        else:
            try:
                raw = await self._file_loader.load_by_object_key(skill_md_asset.object_key)
            except Exception as e:
                raise ToolExecutionError(
                    reason="Skill.md Load Failed",
                    detail_reason=f"Failed to load asset: {type(e).__name__}",
                    retryable=True,
                    metadata={"skill_id": skill.skill_id, "object_key": skill_md_asset.object_key, "detail": str(e)},
                )

        try:
            skill_md = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ToolExecutionError(
                reason="Skill.md Not UTF-8 Text",
                detail_reason=(
                    f"Skill.md of skill '{skill.skill_id}' appears not to be a "
                    f"UTF-8 encoded text file and cannot be parsed."
                ),
                retryable=False,
                metadata={"skill_id": skill.skill_id, "bytes": len(raw)},
            ) from e

        return skill_md
