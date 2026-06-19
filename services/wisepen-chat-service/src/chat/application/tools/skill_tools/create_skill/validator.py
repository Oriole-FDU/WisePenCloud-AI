from __future__ import annotations

from markdown_it import MarkdownIt

from chat.application.tools.skill_tools.create_skill.models import (
    CreateSkillRequest,
    SkillFile,
    SkillSection,
)

_md = MarkdownIt()


def validate_create_skill(request: CreateSkillRequest) -> list[str]:
    """业务校验：返回错误列表，空列表表示通过。

    仅检查 JSON Schema 无法表达的跨字段语义规则：
    1. node_id 全树唯一
    2. body 中不得包含 Markdown 标题
    3. references / assets / scripts 路径合法性
    """
    errors: list[str] = []
    # 1. 检查 node_id 全树唯一性（SKILL.md + 所有 .md 文件）
    seen: dict[str, str] = {}  # node_id -> 首次出现路径
    _collect_node_ids(request.children, "SKILL.md", seen, errors)
    for ref in request.references:
        if ref.path.endswith(".md"):
            _collect_node_ids(ref.children, f"references/{ref.path}", seen, errors)
    for asset in request.assets:
        if asset.path.endswith(".md"):
            _collect_node_ids(asset.children, f"assets/{asset.path}", seen, errors)

    # 2. 检查 body 中不含 Markdown 标题
    _check_body_headings(request.body, "root body", errors)
    for section in request.children:
        _check_section_headings(section, "SKILL.md", errors)
    for ref in request.references:
        if ref.path.endswith(".md"):
            _check_body_headings(ref.body, f"references/{ref.path} body", errors)
            for section in ref.children:
                _check_section_headings(section, f"references/{ref.path}", errors)
    for asset in request.assets:
        if asset.path.endswith(".md"):
            _check_body_headings(asset.body, f"assets/{asset.path} body", errors)
            for section in asset.children:
                _check_section_headings(section, f"assets/{asset.path}", errors)

    # 3. 检查路径合法性
    _check_file_paths(request.references, "references", errors)
    _check_file_paths(request.assets, "assets", errors)
    _check_script_paths(request.scripts, errors)

    return errors


def _collect_node_ids(
    sections: list[SkillSection],
    file_path: str,
    seen: dict[str, str],
    errors: list[str],
) -> None:
    """递归收集 node_id，发现重复时记录首次路径和重复路径。"""
    for section in sections:
        path = f"{file_path} > {section.node_id}"
        if section.node_id in seen:
            errors.append(
                f"Duplicate node_id '{section.node_id}': "
                f"first seen at '{seen[section.node_id]}', "
                f"duplicate at '{path}'"
            )
        else:
            seen[section.node_id] = path
        _collect_node_ids(section.children, path, seen, errors)


def _check_body_headings(body: str, location: str, errors: list[str]) -> None:
    """使用 markdown-it-py 解析 body，检测 heading_open token。

    正确区分正文标题与代码块中的 #，不用简单正则扫描。
    """
    if not body.strip():
        return
    tokens = _md.parse(body)
    for token in tokens:
        if token.type == "heading_open":
            errors.append(
                f"Markdown heading found in {location}. "
                f"Headings are not allowed in body fields; "
                f"use the 'heading' field and 'children' structure instead."
            )
            return  # 每个 body 报一次即可


def _check_section_headings(section: SkillSection, file_path: str, errors: list[str]) -> None:
    """递归检查每个 section 的 body。"""
    _check_body_headings(section.body, f"{file_path} > section '{section.node_id}' body", errors)
    for child in section.children:
        _check_section_headings(child, file_path, errors)


def _check_file_paths(files: list[SkillFile], directory: str, errors: list[str]) -> None:
    """检查 references/assets 路径合法性。"""
    for f in files:
        if not f.path.strip():
            errors.append(f"{directory}: path must not be empty")
        if ".." in f.path.split("/"):
            errors.append(f"{directory}/{f.path}: path must not contain '..'")
        if f.path.startswith("/"):
            errors.append(f"{directory}/{f.path}: path must be relative, not absolute")


def _check_script_paths(scripts: list[SkillFile], errors: list[str]) -> None:
    """检查 scripts 路径合法性。"""
    for s in scripts:
        if not s.path.strip():
            errors.append("scripts: path must not be empty")
        if ".." in s.path.split("/"):
            errors.append(f"scripts/{s.path}: path must not contain '..'")
        if s.path.startswith("/"):
            errors.append(f"scripts/{s.path}: path must be relative, not absolute")
