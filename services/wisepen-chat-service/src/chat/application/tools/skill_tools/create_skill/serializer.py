from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath

from chat.application.tools.skill_tools.create_skill.models import (
    SkillFile,
    SkillScript,
    SkillSection,
)


# ---------------------------------------------------------------------------
# SKILL.md 生成
# ---------------------------------------------------------------------------


def serialize_skill_markdown(
    *,
    skill_id: str,
    trigger_description: str,
    title: str,
    body: str,
    children: list[SkillSection],
    user_id: str,
    session_id: str,
    version: int = 1,
    created_at: datetime | None = None,
) -> str:
    """生成 SKILL.md 内容（YAML frontmatter + Markdown body）。

    遵循 Agent Skills 开放规范 (https://agentskills.io/specification)：
    - YAML frontmatter 包含 name / description / metadata
    - 审计字段放在 metadata 中，与正文分离
    - 纯函数：不鉴权、不写存储、不修改索引
    """
    now = created_at or datetime.now(timezone.utc)
    lines: list[str] = ["---", f"name: {skill_id}", "description: |-"]

    # ---- YAML frontmatter ----
    for desc_line in trigger_description.strip().split("\n"):
        lines.append(f"  {desc_line}")
    lines.append("metadata:")
    lines.append(f"  version: \"{version}\"")
    lines.append(f"  user_id: \"{_yaml_escape(user_id)}\"")
    lines.append(f"  session_id: \"{_yaml_escape(session_id)}\"")
    lines.append(f"  created_at: \"{now.isoformat()}\"")
    lines.append(f"  updated_at: \"{now.isoformat()}\"")
    lines.append("---")
    lines.append("")

    # ---- Markdown body ----
    _append_markdown_body(title, body, children, lines)

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# references / assets 中的 .md 文件生成（复用标题树序列化）
# ---------------------------------------------------------------------------


def serialize_skill_file_markdown(
    *,
    title: str,
    body: str,
    children: list[SkillSection],
) -> str:
    """生成 references/ 或 assets/ 中的 .md 文件内容（无 YAML frontmatter）。

    复用标题树序列化逻辑，但不含 frontmatter——frontmatter 仅 SKILL.md 需要。
    """
    lines: list[str] = []
    _append_markdown_body(title, body, children, lines)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 打包为 zip
# ---------------------------------------------------------------------------


def package_skill(
    *,
    skill_id: str,
    trigger_description: str,
    title: str,
    body: str,
    children: list[SkillSection],
    references: list[SkillFile],
    scripts: list[SkillScript],
    assets: list[SkillFile],
    user_id: str,
    session_id: str,
    version: int = 1,
    created_at: datetime | None = None,
) -> bytes:
    """将完整 Skill 目录结构打包为 zip 字节。

    目录结构遵循 Agent Skills 规范：
    {skill_id}/
    ├── SKILL.md
    ├── references/
    │   └── ...
    ├── scripts/
    │   └── ...
    └── assets/
        └── ...
    """
    now = created_at or datetime.now(timezone.utc)
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # SKILL.md
        skill_md = serialize_skill_markdown(
            skill_id=skill_id,
            trigger_description=trigger_description,
            title=title,
            body=body,
            children=children,
            user_id=user_id,
            session_id=session_id,
            version=version,
            created_at=now,
        )
        zf.writestr(f"{skill_id}/SKILL.md", skill_md)

        # references/
        for ref in references:
            content = _render_skill_file(ref)
            zf.writestr(f"{skill_id}/references/{ref.path}", content)

        # scripts/
        for script in scripts:
            zf.writestr(f"{skill_id}/scripts/{script.path}", script.body)

        # assets/
        for asset in assets:
            content = _render_skill_file(asset)
            zf.writestr(f"{skill_id}/assets/{asset.path}", content)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _append_markdown_body(
    title: str,
    body: str,
    children: list[SkillSection],
    lines: list[str],
) -> None:
    """向 lines 追加 Markdown 正文（H1 + body + children 标题树）。"""
    # H1: 文档唯一一级标题
    lines.append(f"# {title}")
    lines.append("")

    # 根 body：一级标题与第一个二级标题之间的正文
    if body.strip():
        lines.append(body.rstrip("\n"))
        lines.append("")

    # 根 children 从二级标题开始
    for section in children:
        _serialize_section(section, level=2, lines=lines)


def _serialize_section(
    section: SkillSection,
    level: int,
    lines: list[str],
) -> None:
    if level <= 6:
        prefix = "#" * level
        lines.append(f"{prefix} {section.heading}")
    else:
        # 超过 H6 降级为粗体标题文本
        lines.append(f"**{section.heading}**")

    lines.append("")

    if section.body.strip():
        lines.append(section.body.rstrip("\n"))
        lines.append("")

    for child in section.children:
        _serialize_section(child, level=level + 1, lines=lines)


def _render_skill_file(f: SkillFile) -> str:
    """渲染 references/assets 中的文件内容。

    .md 文件复用标题树序列化；其他文本文件直接返回 body。
    """
    if f.path.endswith(".md") and (f.children or f.title):
        # .md 文件且有标题树结构：复用序列化
        title = f.title or _title_from_path(f.path)
        return serialize_skill_file_markdown(
            title=title,
            body=f.body,
            children=f.children,
        )
    # 非结构化文件：直接返回 body
    return f.body


def _title_from_path(path: str) -> str:
    """从文件路径推导 H1 标题（去掉扩展名，替换连字符为空格，首字母大写）。"""
    stem = PurePosixPath(path).stem
    return stem.replace("-", " ").replace("_", " ").title()


def _yaml_escape(value: str) -> str:
    """转义 YAML 双引号字符串中的特殊字符。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')
