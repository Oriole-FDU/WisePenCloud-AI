from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.domain.entities import SkillAssetUploadInitAsset
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient
from pydantic import Field


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


def register_upload_skill_draft_asset_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="upload_skill_draft_asset",
        description=(
            "Upload one UTF-8 text asset to an existing Wisepen AIAsset Skill draft. "
            "Use this after create_skill_info or get_skill_info provides resource_id and draft_version. "
            "Pass path as the target directory and name as the file name. "
            "Upload the updated content to modify an existing draft file, or upload new content to add a new draft file. "
            "Call once per file. This tool does not create Skill info, update metadata, upload binary assets, or publish."
        ),
    )
    async def upload_skill_draft_asset(
        resource_id: Annotated[
            str,
            Field(description="Wisepen Skill resource id returned by create_skill_info or get_skill_info."),
        ],
        draft_version: Annotated[
            int,
            Field(description="Draft version returned by create_skill_info or get_skill_info."),
        ],
        path: Annotated[
            str,
            Field(description="Directory POSIX path inside the Skill draft, e.g. '/', '/references', '/scripts'."),
        ],
        name: Annotated[
            str,
            Field(description="File name only, e.g. 'SKILL.md', 'foo.md', 'bar.py'. Must not contain '/'."),
        ],
        content: Annotated[str, Field(description="UTF-8 text content for this single asset.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "resource_id must not be blank.")
        if not isinstance(draft_version, int) or isinstance(draft_version, bool) or draft_version <= 0:
            raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "draft_version must be a positive integer.")

        draft_asset, content_bytes = _parse_draft_asset(path, name, content)
        upload_result = await ai_asset_client.init_upload_skill_assets(
            resource_id=resource_id,
            draft_version=draft_version,
            assets=[draft_asset],
        )
        ticket = next(
            (item for item in upload_result.tickets if item.path == draft_asset.path and item.name == draft_asset.name),
            None,
        )
        if ticket is None:
            raise ServiceException(
                McpErrorCode.AI_ASSET_RESPONSE_INVALID,
                f"AIAsset did not return an upload ticket for path={draft_asset.path} name={draft_asset.name}.",
            )

        if not ticket.flash_uploaded:
            await ai_asset_client.upload_skill_asset_content(
                ticket.put_url,
                content_bytes,
                callback_header=ticket.callback_header,
            )

        return {
            "resource_id": resource_id,
            "draft_version": draft_version,
            "path": draft_asset.path,
            "name": draft_asset.name,
            "asset_id": ticket.asset_id,
            "asset_resource_type": draft_asset.asset_resource_type,
            "bytes": draft_asset.expected_size,
            "flash_uploaded": ticket.flash_uploaded,
            "upload_submitted": True,
        }


def _parse_draft_asset(raw_path: Any, raw_name: Any, raw_content: Any) -> tuple[SkillAssetUploadInitAsset, bytes]:
    # 把 Tool 入参转换为 AIAsset 上传请求
    path = _normalize_asset_path(raw_path)  # 检查 path
    name = _normalize_asset_name(raw_name)  # 检查 name

    # 检查 content
    if not isinstance(raw_content, str):
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "content must be a string.")
    if "\x00" in raw_content:
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "content must be UTF-8 text, not binary data.")

    if path == "/" and name == "SKILL.md":
        _validate_skill_md(raw_content)  # 检查 SKILL.md

    suffix = PurePosixPath(name).suffix.lower()
    asset_resource_type = _ASSET_TYPE_BY_SUFFIX.get(suffix)
    if asset_resource_type is None:
        supported = ", ".join(sorted(_ASSET_TYPE_BY_SUFFIX))
        raise ServiceException(
            McpErrorCode.SKILL_ASSET_INVALID,
            f"unsupported asset extension for {name}; supported extensions: {supported}",
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
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "path must be a string.")

    path = value.strip()
    if not path:
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "path must not be blank.")
    if "\\" in path:
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "path must use POSIX '/' separators, not backslashes.")

    pure_path = PurePosixPath(path)
    normalized = pure_path.as_posix()

    # path 表示资产所在目录，必须是规范的绝对 POSIX 目录路径
    if not pure_path.is_absolute() or pure_path.anchor != "/" or normalized != path or (path != "/" and path.endswith("/")):
        raise ServiceException(
            McpErrorCode.SKILL_ASSET_INVALID,
            f"path must be '/' or an absolute POSIX directory path: {path}",
        )
    if any(part in {".", ".."} for part in pure_path.parts[1:]):
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, f"path contains an unsafe segment: {path}")

    return normalized


def _normalize_asset_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "name must be a string.")

    name = value.strip()
    if not name:
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "name must not be blank.")

    # name 只表示文件名，不能携带目录信息
    if "/" in name or "\\" in name:
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "name must be a file name only, not a path.")
    if name in {".", ".."} or PurePosixPath(name).name != name:
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, f"name is invalid: {name}")

    return name


def _validate_skill_md(content: str) -> None:
    if not content.strip():
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "/SKILL.md must not be blank.")

    # 检查草稿主文件是否具备最基本的 Skill 形态
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise ServiceException(
            McpErrorCode.SKILL_ASSET_INVALID,
            "/SKILL.md must start with YAML frontmatter delimited by --- lines.",
        )
    if not content[match.end():].strip():
        raise ServiceException(McpErrorCode.SKILL_ASSET_INVALID, "/SKILL.md body must not be blank.")
