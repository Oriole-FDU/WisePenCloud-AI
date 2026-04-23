from typing import Any, Dict

from common.logger import log_error, log_fail

from chat.core.config.app_settings import settings
from chat.domain.interfaces.skill_asset_loader import SkillAssetLoader
from chat.domain.interfaces.tool import BaseTool
from chat.domain.repositories import SkillRepository


class LoadSkillAssetTool(BaseTool):
    """
    按 skill_id + 相对路径懒加载 Skill Bundle 内的某个资产（reference / template / 示例等）
    skill_id 必须在 tool_context['allowed_skill_ids']（本轮 matcher 命中的白名单）中，否则拒绝加载，防止 LLM 幻觉
    该 path 必须出现在 Skill.assets_manifest 中（白名单），否则拒绝加载，防止 LLM 幻觉导致越权访问

    SkillAssetLoader 暂用 LocalFSSkillAssetLoader，应迭代为 OssSkillAssetLoader
    """

    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_asset_loader: SkillAssetLoader,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_asset_loader = skill_asset_loader

    @property
    def name(self) -> str:
        return "load_skill_asset"

    @property
    def description(self) -> str:
        return (
            "Lazy-load the content of a specific asset (reference, template, example, etc.) "
            "belonging to a skill that has already been loaded via load_skill. "
            "You must pass a path that appears in the skill's assets manifest; "
            "do NOT invent paths. Only call when SKILL.md explicitly tells you to consult that asset."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
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

    @property
    def is_ephemeral_output(self) -> bool:
        return True

    @property
    def reserved(self) -> bool:
        # 系统保留，不应被用户级 deny 屏蔽
        return True

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        skill_id = (kwargs.get("skill_id") or "").strip()
        path = (kwargs.get("path") or "").strip()
        if not skill_id or not path:
            return "[Tool Error] Missing required arguments: skill_id, path."

        allowed = set(context.get("allowed_skill_ids") or [])
        if skill_id not in allowed:
            log_fail(
                "load_skill_asset 权限校验",
                "skill_id 不在本轮候选白名单",
                skill_id=skill_id,
                path=path,
                allowed=sorted(allowed),
            )
            return (
                f"[Tool Error] Skill '{skill_id}' is not available in this turn. "
                f"Allowed: {sorted(allowed) or 'none'}."
            )

        try:
            skill = await self._skill_repo.get(skill_id)
        except Exception as e:
            log_error("load_skill_asset 查询", e, skill_id=skill_id, path=path)
            return f"[Tool Error] Failed to query skill '{skill_id}': {type(e).__name__}"
        if skill is None:
            return f"[Tool Error] Skill '{skill_id}' not found."

        # Manifest 白名单校验：path 必须是 publish 时冻结在 assets_manifest 里的那些
        manifest_paths = {asset.path for asset in skill.assets_manifest}
        if path not in manifest_paths:
            log_fail(
                "load_skill_asset path 校验",
                "path 不在 assets_manifest 中",
                skill_id=skill_id,
                path=path,
            )
            return (
                f"[Tool Error] Asset path '{path}' is not declared in the assets manifest of skill '{skill_id}'. "
                f"Available: {sorted(manifest_paths) or 'none'}."
            )

        try:
            content = await self._skill_asset_loader.load_asset(
                skill_id=skill_id, version=skill.version, path=path
            )
        except Exception as e:
            log_error("load_skill_asset 读取", e, skill_id=skill_id, version=skill.version, path=path)
            return f"[Tool Error] Failed to read asset: {type(e).__name__}: {e}"

        # 字符截断，防止超长资产撑爆上下文水位
        if len(content) > settings.TOOL_RESULT_MAX_CHARS:
            content = content[: settings.TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"

        return (
            f"[Loaded Asset] skill_id={skill_id} version={skill.version} path={path}\n"
            f"===== ASSET BEGIN =====\n"
            f"{content}\n"
            f"===== ASSET END ====="
        )
