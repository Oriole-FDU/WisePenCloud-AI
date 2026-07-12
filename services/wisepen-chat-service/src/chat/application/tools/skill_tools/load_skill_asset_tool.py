from typing import Any, Dict

from chat.application.tools.core.execution.hooks.base import ToolPreflightHook, ToolPreflightResult
from chat.application.tools.core.llm.invocation import ToolInvocation
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
from chat.application.tools.skill_tools.common import AllowedSkillIdCheck, build_skill_asset_output_placeholder, \
    SkillPermissionCheck
from chat.application.tools.skill_tools.utils.builtin_skills import get_builtin_skill, is_builtin_skill_id, read_builtin_skill_asset
from chat.domain.interfaces.file_loader import FileLoader


class ValidSkillAssetPathCheck(ToolPreflightHook):
    def __init__(self, ai_asset_client: AIAssetClient) -> None:
        self._ai_asset_client = ai_asset_client

    async def check(
            self,
            invocation: ToolInvocation,
            policy: ToolPolicy,
            parameters_schema: ToolParametersSchema,
            context: dict[str, Any],
    ) -> ToolPreflightResult:
        skill_id: str = invocation.tool_call_arguments.get("skill_id")
        path: str = invocation.tool_call_arguments.get("path")

        if is_builtin_skill_id(skill_id):
            skill = get_builtin_skill(skill_id)
        else:
            skill = await self._ai_asset_client.get_published_skill(skill_id)
        if skill is None:
            return ToolPreflightResult(ok=False,
                                       message=f"Skill '{skill_id}' not found.")

        # Manifest path 校验
        path_to_object_key = {asset.path : asset.object_key for asset in skill.assets_manifest}
        if path not in path_to_object_key:
            return ToolPreflightResult(ok=False,
                                       message=f"Asset path '{path}' is not declared for skill '{skill_id}'.")
        else:
            # 将 Path 转化为 ObjectKey 并存至 _skill_asset_object_key
            skill_asset_object_key = path_to_object_key[path]
            if not skill_asset_object_key:
                return ToolPreflightResult(ok=False,
                                           message=f"Failed to find asset '{path}' of corrupted skill '{skill_id}'.")
            else:
                return ToolPreflightResult(ok=True, metadata={"skill_asset_object_key": skill_asset_object_key})

class LoadSkillAssetTool:
    """
    按 skill_id + 相对路径懒加载 Skill Bundle 内的某个资产（reference / template / 示例等）
    skill_id 必须在 tool_context['allowed_skill_ids']（本轮 matcher 命中的白名单）中，否则拒绝加载，防止 LLM 幻觉
    该 path 必须出现在 Skill.assets_manifest 中（白名单），否则拒绝加载，防止 LLM 幻觉导致越权访问
    """

    def __init__(
        self,
        file_loader: FileLoader,
        ai_asset_client: AIAssetClient,
        resource_client: ResourceClient,
    ) -> None:
        self._file_loader = file_loader
        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The slug id of the skill; must match an Available Skill.",
                },
                "path": {
                    "type": "string",
                    "description": "Relative POSIX path of the asset, exactly as listed in the skill's assets manifest (e.g. 'references/citation-styles.md').",
                },
            },
            "required": ["skill_id", "path"],
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="load_skill_asset",
                description=(
                    "Lazy-load the content of a specific asset (reference, template, example, etc.) "
                    "belonging to a skill that has already been loaded via load_skill. "
                    "You must pass a path that appears in the skill's assets manifest; "
                    "do NOT invent paths. Only call when SKILL.md explicitly tells you to consult that asset."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=False,
                persisted_output_placeholder_factory=build_skill_asset_output_placeholder,
                risk_level=ToolRiskLevel.MEDIUM,
                required_context_keys=("allowed_skill_ids",),
                timeout_seconds=8.0,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
            ),
            preflight_hooks=(AllowedSkillIdCheck(), SkillPermissionCheck(resource_client), ValidSkillAssetPathCheck(ai_asset_client)),
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
        path = (kwargs.get("path") or "").strip()

        if is_builtin_skill_id(skill_id):
            # 内置 Skill 使用 read_builtin_skill_asset 加载
            try:
                raw = read_builtin_skill_asset(skill_id, path)
            except Exception as e:
                raise ToolExecutionError(
                    reason="Skill Asset Load Failed",
                    detail_reason=f"Failed to load builtin skill asset: {type(e).__name__}",
                    retryable=False,
                    metadata={"skill_id": skill_id, "path": path, "detail": str(e)},
                )
        else:
            object_key = context["skill_asset_object_key"]
            try:
                raw = await self._file_loader.load_by_object_key(object_key)
            except Exception as e:
                raise ToolExecutionError(
                    reason="Skill Asset Load Failed",
                    detail_reason=f"Failed to load skill asset: {type(e).__name__}",
                    retryable=True,
                    metadata={"skill_id": skill_id, "path": path, "object_key": object_key, "detail": str(e)},
                )

        # Loader 返回 bytes：资产可能是文本（.md / .py / .json）也可能是二进制（.png / .pdf / .wasm ...）
        # 在给 LLM 的边界上做 UTF-8 严格解码，拒绝不可文本化的二进制资产
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolExecutionError(
                reason="Asset Not UTF-8 Text",
                detail_reason=(
                    f"Asset '{path}' of skill '{skill_id}' appears not to be a "
                    f"UTF-8 encoded text file and cannot be parsed."
                ),
                retryable=False,
                metadata={"skill_id": skill_id, "path": path, "bytes": len(raw)},
            )

        return (
            f"[Loaded Skill Asset] skill_id={skill_id} path={path}\n"
            f"<skill_asset>\n"
            f"{content}\n"
            f"</skill_asset>"
        )
