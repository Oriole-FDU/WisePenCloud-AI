from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.core.config.app_settings import settings
from chat.domain.entities.skill import SkillAssetUploadInitAsset
from chat.service_client import AIAssetClient


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_ASSET_TYPE_BY_SUFFIX = {
    ".md": "MD",
    ".py": "PYTHON_SCRIPT",
    ".txt": "TEXT",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
}



class SkillDraftAssetValidationError(ValueError):
    pass


class UploadSkillDraftAssetTool:
    def __init__(self, ai_asset_client: AIAssetClient) -> None:
        self._ai_asset_client = ai_asset_client
        parameters_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Wisepen Skill resource id returned by create_skill_info or get_skill_info.",
                },
                "draft_version": {
                    "type": "integer",
                    "description": "Draft version returned by create_skill_info or get_skill_info.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory POSIX path inside the Skill draft, e.g. '/', '/references', '/scripts'.",
                },
                "name": {
                    "type": "string",
                    "description": "File name only, e.g. 'SKILL.md', 'foo.md', 'bar.py'. Must not contain '/'.",
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 text content for this single asset.",
                },
            },
            "required": ["resource_id", "draft_version", "path", "name", "content"],
            "additionalProperties": False,
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="upload_skill_draft_asset",
                description=(
                    "Upload one UTF-8 text asset to an existing Wisepen AIAsset Skill draft. "
                    "Use this after create_skill_info or get_skill_info provides resource_id and draft_version. "
                    "Pass path as the target directory and name as the file name. "
                    "Upload the updated content to modify an existing draft file, or upload new content to add a new draft file. "
                    "Call once per file. This tool does not create Skill info, update metadata, upload binary assets, or publish."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.HIGH,
                required_context_keys=("allowed_skill_ids",),
                required_allowed_builtin_skill_ids=("builtin:skill-creator",),
                timeout_seconds=30.0,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
            ),
            preflight_hooks=(),
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
        try:
            resource_id = str(kwargs.get("resource_id"))
            draft_version = kwargs.get("draft_version")
            if not isinstance(draft_version, int) or isinstance(draft_version, bool) or draft_version <= 0:
                raise SkillDraftAssetValidationError("draft_version must be a positive integer.")
            draft_asset, content_bytes = _parse_draft_asset(kwargs.get("path"), kwargs.get("name"), kwargs.get("content"))
        except SkillDraftAssetValidationError as e:
            raise ToolExecutionError(
                reason="Skill Draft Asset Validation Failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

        try:
            # 获取上传 URL
            upload_result = await self._ai_asset_client.init_upload_skill_assets(
                resource_id=resource_id,
                draft_version=draft_version,
                assets=[draft_asset],
            )
            if upload_result is None:
                raise ToolExecutionError(
                    reason="Skill Draft Upload Ticket Missing",
                    detail_reason="AIAsset did not return an upload initialization result.",
                    retryable=False,
                    metadata={"resource_id": resource_id, "draft_version": draft_version},
                )

            ticket = next((item for item in upload_result.tickets if item.path == draft_asset.path and item.name == draft_asset.name), None)
            if not ticket.flash_uploaded:
                await self._ai_asset_client.upload_skill_asset_content(ticket.put_url, content_bytes, callback_header=ticket.callback_header)

        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                reason="Skill Draft Asset Upload Failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

        return (
                f"[Uploaded Skill Draft Asset] resource_id={resource_id} draft_version={draft_version} path={draft_asset.path} name={draft_asset.name} "
                f"asset_id={ticket.asset_id} asset_resource_type={draft_asset.asset_resource_type} bytes={draft_asset.expected_size} "
                f"flash_uploaded={ticket.flash_uploaded}"
        )



def _parse_draft_asset(raw_path: Any, raw_name: Any, raw_content: Any) -> tuple[SkillAssetUploadInitAsset, bytes]:
    # 把 Tool 入参转换为 AIAsset 上传请求
    path = _normalize_asset_path(raw_path) # 检查 Path
    name = _normalize_asset_name(raw_name) # 检查 Name
    # 检查 Content
    if not isinstance(raw_content, str):
        raise SkillDraftAssetValidationError("content must be a string.")
    if "\x00" in raw_content:
        raise SkillDraftAssetValidationError("content must be UTF-8 text, not binary data.")

    if path == "/" and name == "SKILL.md":
        _validate_skill_md(raw_content) # 检查 Skill.md

    suffix = PurePosixPath(name).suffix.lower()
    asset_resource_type = _ASSET_TYPE_BY_SUFFIX.get(suffix)
    if asset_resource_type is None:
        supported = ", ".join(sorted(_ASSET_TYPE_BY_SUFFIX))
        raise SkillDraftAssetValidationError(
            f"unsupported asset extension for {name}; supported extensions: {supported}"
        )

    content_bytes = raw_content.encode("utf-8")
    return (
        SkillAssetUploadInitAsset(
            path=path,
            name=name,
            asset_resource_type=asset_resource_type,
            md5=hashlib.md5(content_bytes).hexdigest(),
            expected_size=len(content_bytes),
        ),
        content_bytes,
    )


def _normalize_asset_path(value: Any) -> str:
    if not isinstance(value, str):
        raise SkillDraftAssetValidationError("path must be a string.")

    path = value.strip()
    if not path:
        raise SkillDraftAssetValidationError("path must not be blank.")
    if "\\" in path:
        raise SkillDraftAssetValidationError("path must use POSIX '/' separators, not backslashes.")

    pure_path = PurePosixPath(path)
    normalized = pure_path.as_posix()

    # path 表示资产所在目录，必须是规范的绝对 POSIX 目录路径
    if not pure_path.is_absolute() or pure_path.anchor != "/" or normalized != path or (path != "/" and path.endswith("/")):
        raise SkillDraftAssetValidationError(f"path must be '/' or an absolute POSIX directory path: {path}")
    if any(part in {".", ".."} for part in pure_path.parts[1:]):
        raise SkillDraftAssetValidationError(f"path contains an unsafe segment: {path}")

    return normalized


def _normalize_asset_name(value: Any) -> str:
    if not isinstance(value, str):
        raise SkillDraftAssetValidationError("name must be a string.")

    name = value.strip()
    if not name:
        raise SkillDraftAssetValidationError("name must not be blank.")

    # name 只表示文件名，不能携带目录信息
    if "/" in name or "\\" in name:
        raise SkillDraftAssetValidationError("name must be a file name only, not a path.")
    if name in {".", ".."} or PurePosixPath(name).name != name:
        raise SkillDraftAssetValidationError(f"name is invalid: {name}")

    return name


def _validate_skill_md(content: str) -> None:
    if not content.strip():
        raise SkillDraftAssetValidationError("/SKILL.md must not be blank.")

    # 检查草稿主文件是否具备最基本的 Skill 形态
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise SkillDraftAssetValidationError("/SKILL.md must start with YAML frontmatter delimited by --- lines.")
    if not content[match.end():].strip():
        raise SkillDraftAssetValidationError("/SKILL.md body must not be blank.")
