from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from chat.domain.entities import Skill, SkillAssetMeta, SkillMeta


BUILTIN_SKILL_ID_PREFIX = "builtin:"
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parents[4] / "builtin_skills"

_BUILTIN_ASSET_KIND_BY_SUFFIX = {
    ".md": "MD",
    ".py": "PYTHON_SCRIPT",
    ".txt": "TEXT",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
}


def _get_builtin_skill_root(skill_id: str) -> Path | None:
    if not is_builtin_skill_id(skill_id):
        return None
    skill_slug = skill_id.removeprefix(BUILTIN_SKILL_ID_PREFIX)
    if not skill_slug:
        return None
    skill_root = (BUILTIN_SKILLS_DIR / skill_slug).resolve()
    builtin_root = BUILTIN_SKILLS_DIR.resolve()
    try:
        skill_root.relative_to(builtin_root)
    except ValueError:
        return None
    return skill_root


def is_builtin_skill_id(skill_id: str | None) -> bool:
    return bool(skill_id and skill_id.startswith(BUILTIN_SKILL_ID_PREFIX))


def get_builtin_skill_meta(skill_id: str) -> SkillMeta | None:
    skill = get_builtin_skill(skill_id)
    if skill is None:
        return None
    return SkillMeta(
        skill_id=skill.skill_id,
        name=skill.name,
        description=skill.description,
        version=skill.version,
    )


@lru_cache(maxsize=128)
def get_builtin_skill(skill_id: str) -> Skill | None:
    skill_root = _get_builtin_skill_root(skill_id)
    if skill_root is None:
        return None

    skill_md_path = skill_root / "SKILL.md"
    if not skill_md_path.is_file():
        return None

    skill_md = skill_md_path.read_text(encoding="utf-8")
    lines = skill_md.splitlines()
    frontmatter = {}
    if lines and lines[0].strip() == "---":
        try:
            end = lines[1:].index("---") + 1
            loaded = yaml.safe_load("\n".join(lines[1:end])) or {}
            if isinstance(loaded, dict):
                frontmatter = loaded
        except ValueError:
            frontmatter = {}

    assets_manifest = []
    for file_path in sorted(path for path in skill_root.rglob("*") if path.is_file()):
        rel_path = file_path.relative_to(skill_root).as_posix()
        manifest_path = "/SKILL.md" if rel_path == "SKILL.md" else rel_path
        assets_manifest.append(
            SkillAssetMeta(
                id=f"{skill_id}:{rel_path}",
                path=manifest_path,
                object_key=f"{skill_id}:{rel_path}",
                kind=_BUILTIN_ASSET_KIND_BY_SUFFIX.get(file_path.suffix.lower(), "TEXT"),
                upload_status="COMPLETED",
                description="",
                size_bytes=file_path.stat().st_size,
            )
        )

    return Skill(
        skill_id=skill_id,
        name=str(frontmatter.get("name") or skill_id.removeprefix(BUILTIN_SKILL_ID_PREFIX)),
        description=str(frontmatter.get("description") or ""),
        source_type="BUILTIN",
        assets_manifest=assets_manifest,
        version=1,
    )


def read_builtin_skill_asset(skill_id: str, asset_path: str) -> bytes:
    skill_root = _get_builtin_skill_root(skill_id)
    if skill_root is None:
        raise FileNotFoundError(f"builtin skill not found: {skill_id}")
    rel_path = "SKILL.md" if asset_path == "/SKILL.md" else asset_path.strip("/")
    return (skill_root / rel_path).read_bytes()
