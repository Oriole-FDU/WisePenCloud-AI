import shutil
import tempfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Protocol

import httpx
import yaml

from common.core.constants import SecurityConstants
from common.http.rpc_client import RpcClient

from .errors import (
    SkillBundleBuildError,
    SkillBundleMarkdownRenderError,
    SkillBundlePathError,
)
from .models import (
    CreateSkillBundleRequest,
    CreateSkillBundleResult,
    SkillBundleAssetDraft,
    SkillBundleBuildContext,
    SkillBundleFileSummary,
    SkillBundleReferenceDraft,
    SkillBundleScriptDraft,
    SkillMarkdownDraft,
    SkillMarkdownExample,
    SkillWorkflowStep,
)


class SkillBundleArtifactStore(Protocol):
    """
    Skill Bundle 产物存储接口。

    Args:
    - skill_root: 构建完成的 skill 目录路径。
    - skill_id: Skill 目录名。
    - version: Bundle 版本。
    """

    async def save_bundle(
        self,
        *,
        skill_root: Path,
        skill_id: str,
        version: str,
    ) -> str:
        pass


class DevSkillBundleArtifactStore:
    """
    开发环境 Skill Bundle 产物存储。

    - 将 skill 目录树原样复制到 dev_fixtures/skill_bundles/<skill_id>/<version>/。
    - 目录结构与 LocalFSSkillAssetLoader、seed_demo_scripts 保持一致。
    - 返回目标目录的绝对路径字符串。
    - 生产环境替换为 OSS / file handoff 等实现。
    """

    def __init__(self, output_root: Path) -> None:
        self._output_root = output_root

    async def save_bundle(
        self,
        *,
        skill_root: Path,
        skill_id: str,
        version: str,
    ) -> str:
        self._ensure_safe_segment(skill_id, kind="skill_id")
        self._ensure_safe_segment(version, kind="version")

        target_dir = (self._output_root / skill_id / version).resolve()
        try:
            target_dir.relative_to(self._output_root)
        except ValueError:
            raise SkillBundlePathError(
                "target_dir escapes output root directory"
            )

        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.copytree(skill_root, target_dir)

        return str(target_dir.resolve())

    @staticmethod
    def _ensure_safe_segment(segment: str, *, kind: str) -> None:
        if not segment:
            raise SkillBundlePathError(f"{kind} must be non-empty")
        if "/" in segment or "\\" in segment or segment in (".", ".."):
            raise SkillBundlePathError(f"Illegal {kind}: {segment!r}")


class SkillMarkdownRenderer:
    """
    SKILL.md 渲染器。

    - frontmatter 来自 request + trusted context。
    - body 来自 SkillMarkdownDraft。
    """
    def render(
        self,
        *,
        request: CreateSkillBundleRequest,
        context: SkillBundleBuildContext,
    ) -> str:
        try:
            frontmatter = self._render_frontmatter(
                request=request,
                context=context,
            )
            body = self._render_body(
                display_name=request.display_name,
                markdown=request.markdown,
                references=request.references,
                assets=request.assets,
                scripts=request.scripts,
            )

            return f"{frontmatter}\n{body}".rstrip() + "\n"
        except Exception as e:
            raise SkillBundleMarkdownRenderError(
                f"Failed to render SKILL.md: {type(e).__name__}: {e}"
            ) from e

    def _render_frontmatter(
        self,
        *,
        request: CreateSkillBundleRequest,
        context: SkillBundleBuildContext,
    ) -> str:
        data: Dict[str, Any] = {
            "name": request.skill_id,
            "display_name": request.display_name,
            "description": request.description,
            "version": request.version,
            "metadata": {
                "schema_version": "1.0",
                "target_runtime": "wisepen",
                "generated_by": "agent",
                "created_by": {
                    "user_id": context.user_id,
                },
                "source": {
                    "service": "chat-service",
                    "session_id": context.session_id,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        yaml_text = yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
        ).strip()

        return f"---\n{yaml_text}\n---"

    def _render_body(
        self,
        *,
        display_name: str,
        markdown: SkillMarkdownDraft,
        references: List[SkillBundleReferenceDraft],
        assets: List[SkillBundleAssetDraft],
        scripts: List[SkillBundleScriptDraft],
    ) -> str:
        lines: List[str] = []

        lines.append(f"# {display_name}")
        lines.append("")

        self._append_text_section(
            lines=lines,
            title="Purpose",
            content=markdown.purpose,
        )
        self._append_bullet_section(
            lines=lines,
            title="Input requirements",
            items=markdown.input_requirements,
        )
        self._append_workflow_section(
            lines=lines,
            workflow=markdown.workflow,
        )
        self._append_bullet_section(
            lines=lines,
            title="Output requirements",
            items=markdown.output_requirements,
        )
        self._append_bullet_section(
            lines=lines,
            title="Tool guidance",
            items=markdown.tool_guidance,
        )
        self._append_bullet_section(
            lines=lines,
            title="Resource guidance",
            items=markdown.resource_guidance,
        )
        self._append_bundled_files_section(
            lines=lines,
            references=references,
            assets=assets,
            scripts=scripts,
        )
        self._append_bullet_section(
            lines=lines,
            title="Constraints",
            items=markdown.constraints,
        )
        self._append_examples_section(
            lines=lines,
            examples=markdown.examples,
        )

        return "\n".join(lines).rstrip() + "\n"

    def _append_text_section(
        self,
        *,
        lines: List[str],
        title: str,
        content: str,
    ) -> None:
        if not content:
            return

        lines.append(f"## {title}")
        lines.append("")
        lines.append(content)
        lines.append("")

    def _append_bullet_section(
        self,
        *,
        lines: List[str],
        title: str,
        items: List[str],
    ) -> None:
        if not items:
            return

        lines.append(f"## {title}")
        lines.append("")

        for item in items:
            lines.append(f"- {item}")

        lines.append("")

    def _append_workflow_section(
        self,
        *,
        lines: List[str],
        workflow: List[SkillWorkflowStep],
    ) -> None:
        if not workflow:
            return

        lines.append("## Workflow")
        lines.append("")

        for index, step in enumerate(workflow, start=1):
            lines.append(f"{index}. {step.step}")
            for sub_step in step.sub_steps:
                lines.append(f"   - {sub_step}")

        lines.append("")

    def _append_bundled_files_section(
        self,
        *,
        lines: List[str],
        references: List[SkillBundleReferenceDraft],
        assets: List[SkillBundleAssetDraft],
        scripts: List[SkillBundleScriptDraft],
    ) -> None:
        if not references and not assets and not scripts:
            return

        lines.append("## Bundled files")
        lines.append("")

        for reference in references:
            path = f"references/{reference.relative_path}"
            lines.append(f"- `{path}` — {reference.description}")

        for asset in assets:
            path = f"assets/{asset.relative_path}"
            lines.append(f"- `{path}` — {asset.description}")

        for script in scripts:
            path = f"scripts/{script.relative_path}"
            lines.append(f"- `{path}` — {script.description}")

        lines.append("")

    def _append_examples_section(
        self,
        *,
        lines: List[str],
        examples: List[SkillMarkdownExample],
    ) -> None:
        if not examples:
            return

        lines.append("## Examples")
        lines.append("")

        for example in examples:
            lines.append(f"### {example.title}")
            lines.append("")
            lines.append("**User input**")
            lines.append("")
            lines.append(example.user_input)
            lines.append("")
            lines.append("**Expected behavior**")
            lines.append("")
            lines.append(example.expected_behavior)
            lines.append("")


class SkillBundleService:
    """
    Skill Bundle 构建服务。

    - 输入结构化 draft。
    - 渲染 SKILL.md。
    - 写入 references/、assets/、scripts/。
    - 通过 artifact store 保存 skill 目录。
    """

    def __init__(
        self,
        artifact_store: SkillBundleArtifactStore,
        renderer: SkillMarkdownRenderer,
        rpc_client: RpcClient,
    ) -> None:
        self._artifact_store = artifact_store
        self._renderer = renderer
        self._rpc_client = rpc_client

    async def create_bundle(
        self,
        *,
        request: CreateSkillBundleRequest,
        context: SkillBundleBuildContext,
    ) -> CreateSkillBundleResult:
        with tempfile.TemporaryDirectory(prefix="skill-bundle-") as tmp:
            try:
                tmp_dir = Path(tmp)
                skill_root = tmp_dir / request.skill_id
                skill_root.mkdir(parents=True, exist_ok=True)

                files: List[SkillBundleFileSummary] = []

                skill_md = self._renderer.render(
                    request=request,
                    context=context,
                )
                skill_md_path = skill_root / "SKILL.md"
                skill_md_path.write_text(skill_md, encoding="utf-8")
                files.append(
                    SkillBundleFileSummary(
                        path="SKILL.md",
                        size_bytes=len(skill_md.encode("utf-8")),
                        description="Skill instructions and metadata.",
                    )
                )

                files.extend(
                    self._write_references(
                        skill_root=skill_root,
                        references=request.references,
                    )
                )
                files.extend(
                    self._write_assets(
                        skill_root=skill_root,
                        assets=request.assets,
                    )
                )
                files.extend(
                    self._write_scripts(
                        skill_root=skill_root,
                        scripts=request.scripts,
                    )
                )

                bundle_file_ref = await self._artifact_store.save_bundle(
                    skill_root=skill_root,
                    skill_id=request.skill_id,
                    version=request.version,
                )
                await self._publish_to_asset_service(
                    request=request,
                    context=context,
                    skill_root=skill_root,
                    files=files,
                )

                return CreateSkillBundleResult(
                    skill_id=request.skill_id,
                    display_name=request.display_name,
                    version=request.version,
                    bundle_dir_ref=bundle_file_ref,
                    files=files,
                )
            except SkillBundleBuildError:
                raise
            except Exception as e:
                raise SkillBundleBuildError(
                    f"Failed to create skill bundle: {type(e).__name__}: {e}"
                ) from e

    def _write_references(
        self,
        *,
        skill_root: Path,
        references: List[SkillBundleReferenceDraft],
    ) -> List[SkillBundleFileSummary]:
        summaries: List[SkillBundleFileSummary] = []

        for reference in references:
            output_path = self._resolve_bundle_path(
                root=skill_root / "references",
                relative_path=reference.relative_path,
            )
            self._write_text(output_path, reference.content)
            summaries.append(
                SkillBundleFileSummary(
                    path=f"references/{reference.relative_path}",
                    size_bytes=len(reference.content.encode("utf-8")),
                    description=reference.description,
                )
            )

        return summaries

    def _write_assets(
        self,
        *,
        skill_root: Path,
        assets: List[SkillBundleAssetDraft],
    ) -> List[SkillBundleFileSummary]:
        summaries: List[SkillBundleFileSummary] = []

        for asset in assets:
            output_path = self._resolve_bundle_path(
                root=skill_root / "assets",
                relative_path=asset.relative_path,
            )
            self._write_text(output_path, asset.content_text)
            summaries.append(
                SkillBundleFileSummary(
                    path=f"assets/{asset.relative_path}",
                    size_bytes=len(asset.content_text.encode("utf-8")),
                    description=asset.description,
                )
            )

        return summaries

    def _write_scripts(
        self,
        *,
        skill_root: Path,
        scripts: List[SkillBundleScriptDraft],
    ) -> List[SkillBundleFileSummary]:
        summaries: List[SkillBundleFileSummary] = []

        for script in scripts:
            output_path = self._resolve_bundle_path(
                root=skill_root / "scripts",
                relative_path=script.relative_path,
            )
            self._write_text(output_path, script.content)
            summaries.append(
                SkillBundleFileSummary(
                    path=f"scripts/{script.relative_path}",
                    size_bytes=len(script.content.encode("utf-8")),
                    description=script.description,
                )
            )

        return summaries

    def _resolve_bundle_path(
        self,
        *,
        root: Path,
        relative_path: str,
    ) -> Path:
        if not relative_path:
            raise SkillBundlePathError("relative_path is required")

        if "\\" in relative_path:
            raise SkillBundlePathError("relative_path must use POSIX '/' separators")

        posix_path = PurePosixPath(relative_path)
        if posix_path.is_absolute():
            raise SkillBundlePathError("relative_path must be relative")

        if ".." in posix_path.parts:
            raise SkillBundlePathError("relative_path must not contain '..'")

        if posix_path.name == "":
            raise SkillBundlePathError("relative_path must point to a file")

        root_resolved = root.resolve()
        target = (root / Path(*posix_path.parts)).resolve()

        if target == root_resolved or root_resolved not in target.parents:
            raise SkillBundlePathError(
                f"relative_path escapes bundle directory: {relative_path}"
            )

        return target

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def _publish_to_asset_service(
        self,
        *,
        request: CreateSkillBundleRequest,
        context: SkillBundleBuildContext,
        skill_root: Path,
        files: List[SkillBundleFileSummary],
    ) -> None:
        headers = {SecurityConstants.HEADER_USER_ID: context.user_id}
        resource_id = await self._rpc_client.post(
            "wisepen-ai-asset-service",
            "/skill/createSkill",
            json={
                "title": request.display_name,
                "name": request.skill_id,
                "description": request.description,
                "sourceType": "BY_AGENT",
            },
            headers=headers,
        )

        assets = []
        file_bytes_by_path: Dict[str, bytes] = {}
        for file in files:
            rel_path = PurePosixPath(file.path)
            raw = (skill_root / Path(*rel_path.parts)).read_bytes()
            file_bytes_by_path[file.path] = raw
            assets.append(
                {
                    "name": rel_path.name,
                    "path": "/" if str(rel_path.parent) == "." else f"/{rel_path.parent.as_posix()}",
                    "skillAssetResourceType": self._asset_resource_type(rel_path.name),
                    "md5": hashlib.md5(raw).hexdigest(),
                    "expectedSize": len(raw),
                }
            )

        upload_init = await self._rpc_client.post(
            "wisepen-ai-asset-service",
            "/skill/initUploadSkillAssets",
            json={
                "resourceId": resource_id,
                "draftVersion": 1,
                "assets": assets,
            },
            headers=headers,
            timeout=20.0,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            for ticket in upload_init.get("assetUploadTickets", []):
                if ticket.get("flashUploaded"):
                    continue
                put_url = ticket.get("putUrl")
                if not put_url:
                    raise SkillBundleBuildError("Skill asset upload ticket missing putUrl")
                rel_file = self._ticket_relative_path(ticket)
                put_headers = {"Content-Type": "application/octet-stream"}
                if ticket.get("callbackHeader"):
                    put_headers["x-oss-callback"] = ticket["callbackHeader"]
                response = await client.put(
                    put_url,
                    content=file_bytes_by_path[rel_file],
                    headers=put_headers,
                )
                response.raise_for_status()

        await self._rpc_client.post(
            "wisepen-ai-asset-service",
            "/skill/publishSkillVersion",
            json={
                "resourceId": resource_id,
                "draftVersion": upload_init.get("version", 1),
            },
            headers=headers,
            timeout=20.0,
        )

    @staticmethod
    def _ticket_relative_path(ticket: Dict[str, Any]) -> str:
        path = str(ticket.get("path") or "/").strip("/")
        name = str(ticket["name"])
        return name if not path else f"{path}/{name}"

    @staticmethod
    def _asset_resource_type(name: str) -> str:
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == ".md":
            return "MD"
        if suffix == ".py":
            return "PYTHON_SCRIPT"
        if suffix == ".json":
            return "JSON"
        if suffix in {".yaml", ".yml"}:
            return "YAML"
        if suffix == ".toml":
            return "TOML"
        return "TEXT"
