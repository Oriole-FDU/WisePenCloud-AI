"""
>>> TEMPORARY DEV SEEDER — REMOVE ONCE wisepen-skill-service (Java) IS READY <<<

职责边界澄清：
    - 在最终形态里，Skill collection（wisepen_published_skill）的生命周期（作者贡献、审核、
      启停、发布、skill_md 同步、OSS 资产上传）完全由 Java wisepen-skill-service 负责。
    - 本 chat-service 只负责**只读消费**。
    - 本脚本仅为开发期向 Mongo 写入 demo Skill 数据，让 AI 端可以单独跑通
      Matcher / LoadSkillTool / LoadSkillAssetTool 链路。当 Java 服务上线后，
      本文件应**直接删除**，seeding 通过 Java 服务的管理界面或 CLI 完成。

运行方式（示例）：
    cd services/wisepen-chat-service
    python -m chat.scripts.seed_demo_skills

它做的事：
    1. 扫描 dev_fixtures/skill_bundles/<skill_id>/<version>/ 下每个 bundle
    2. 读取 SKILL.md（含 YAML frontmatter）解析出 metadata
    3. 把除 SKILL.md 以外的文件登记成 assets_manifest
    4. upsert 进 wisepen_published_skill collection
    5. 为每个 asset 及 SKILL.md 写入与 Java 发布侧一致的 OSS object_key 约定：
       skills/<skill_id>/<version>/<相对路径>；正文不拷贝进 Mongo
    6. DEV 运行期 LocalFSSkillAssetLoader 先从 SKILL_ASSETS_CACHE_DIR 读盘，未命中回退 OSS；
       生产形态（DEV=False）由 Java 上传对象后 chat 经 file-storage 预签名读取并落本地磁盘缓存
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import yaml
from beanie import init_beanie
from pymongo import AsyncMongoClient

from common.logger import log_error, log_event

from chat.domain.entities.skill import Skill, SkillAssetMeta


# 把 MD 文件归类成 SkillAssetMeta.kind 的简单启发式；Java 服务会按更正式的 schema 填。
_KIND_BY_FIRST_SEGMENT = {
    "references": "reference",
    "templates": "template",
    "scripts": "script",
    "examples": "example",
}


def _object_key(skill_id: str, version: str, rel_path: str) -> str:
    """与 Java wisepen-skill-service 发布侧约定一致：skills/<skill_id>/<version>/<path>"""
    return f"skills/{skill_id}/{version}/{rel_path}"


def _split_frontmatter(text: str) -> Tuple[dict, str]:
    """最小化 frontmatter 解析：`---\\n...\\n---\\n<body>`；无 frontmatter 则返回 ({}, text)。"""
    if not text.startswith("---"):
        return {}, text
    # 按首个 `---` 之后的第二个 `---` 切分
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text
    fm_text = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    meta = yaml.safe_load(fm_text) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


def _scan_assets(version_dir: Path, skill_id: str, version: str) -> List[SkillAssetMeta]:
    """扫描 bundle 目录下 SKILL.md 以外的文件，生成 assets manifest（POSIX 相对路径 + object_key）。"""
    assets: List[SkillAssetMeta] = []
    for p in sorted(version_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(version_dir).as_posix()
        if rel == "SKILL.md":
            continue
        first_seg = rel.split("/", 1)[0]
        kind = _KIND_BY_FIRST_SEGMENT.get(first_seg, "other")
        assets.append(
            SkillAssetMeta(
                path=rel,
                object_key=_object_key(skill_id, version, rel),
                kind=kind,
                description="(dev fixture, no description)",
                size_bytes=p.stat().st_size,
            )
        )
    return assets


async def _seed_one_bundle(version_dir: Path, skill_id: str, version: str) -> None:
    skill_md_path = version_dir / "SKILL.md"
    if not skill_md_path.is_file():
        log_error(
            "seed_demo_skills: 跳过，缺 SKILL.md",
            FileNotFoundError(str(skill_md_path)),
            skill_id=skill_id,
            version=version,
        )
        return

    raw = skill_md_path.read_text(encoding="utf-8")
    meta, _body = _split_frontmatter(raw)

    # skill_md 字段按约定存**完整文件**（含 frontmatter），让 LLM 拿到第一视角的原始说明
    skill_md_full = raw

    display_name = meta.get("display_name") or meta.get("name") or skill_id
    description = meta.get("description") or ""
    triggers = meta.get("triggers") or []
    if not isinstance(triggers, list):
        triggers = []
    enabled = bool(meta.get("enabled", True))

    # 版本优先使用 frontmatter 里声明的；目录名 fallback。保持两者一致有助于人肉审计。
    declared_version = str(meta.get("version") or version)
    if declared_version != version:
        log_event(
            "seed_demo_skills: frontmatter version 与目录不一致，以目录为准",
            skill_id=skill_id,
            dir_version=version,
            declared_version=declared_version,
        )

    assets_manifest = _scan_assets(version_dir, skill_id=skill_id, version=version)
    skill_md_object_key = _object_key(skill_id, version, "SKILL.md")

    existing = await Skill.find_one(Skill.skill_id == skill_id)
    now = datetime.now(timezone.utc)
    if existing is None:
        doc = Skill(
            skill_id=skill_id,
            display_name=display_name,
            description=description,
            triggers=[str(t) for t in triggers],
            skill_md=skill_md_full,
            skill_md_object_key=skill_md_object_key,
            assets_manifest=assets_manifest,
            version=version,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        await doc.insert()
        log_event("seed_demo_skills: insert", skill_id=skill_id, version=version, assets=len(assets_manifest))
    else:
        existing.display_name = display_name
        existing.description = description
        existing.triggers = [str(t) for t in triggers]
        existing.skill_md = skill_md_full
        existing.skill_md_object_key = skill_md_object_key
        existing.assets_manifest = assets_manifest
        existing.version = version
        existing.enabled = enabled
        existing.updated_at = now
        await existing.save()
        log_event("seed_demo_skills: update", skill_id=skill_id, version=version, assets=len(assets_manifest))


async def _main() -> None:
    default_root = (
        Path(__file__).resolve().parents[3] / "dev_fixtures" / "skill_bundles"
    )
    root = Path(os.environ.get("SKILL_ASSETS_CACHE_PATH", str(default_root))).resolve()
    if not root.is_dir():
        log_error(
            "seed_demo_skills: SKILL_ASSETS_CACHE_PATH 不存在，退出",
            FileNotFoundError(str(root)),
            path=str(root),
        )
        return

    mongo_url = os.environ.get(
        "SEED_MONGO_URL", "mongodb://root:root@localhost:27017/"
    )
    mongo_client = AsyncMongoClient(mongo_url)
    await init_beanie(
        database=mongo_client["wisepen_chat"],
        document_models=[Skill],
    )

    seeded = 0
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        skill_id = skill_dir.name
        for version_dir in sorted(p for p in skill_dir.iterdir() if p.is_dir()):
            version = version_dir.name
            await _seed_one_bundle(version_dir, skill_id=skill_id, version=version)
            seeded += 1

    log_event("seed_demo_skills: 完成", bundles=seeded, root=str(root))


if __name__ == "__main__":
    asyncio.run(_main())
