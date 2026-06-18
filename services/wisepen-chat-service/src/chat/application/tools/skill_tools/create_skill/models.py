from __future__ import annotations

from pydantic import BaseModel


class SkillSection(BaseModel):
    node_id: str
    heading: str
    body: str
    children: list[SkillSection]


class SkillFile(BaseModel):
    """references/ 或 assets/ 中的文件。

    .md 文件通过 body + children 生成结构化 Markdown（复用标题树序列化）；
    其他文本文件直接使用 body，children 留空。
    """

    path: str
    title: str | None = None  # .md 文件的 H1 标题；省略则从 path 推导
    body: str
    children: list[SkillSection] = []


class SkillScript(BaseModel):
    """scripts/ 中的可执行脚本。"""

    path: str
    body: str


class CreateSkillRequest(BaseModel):
    skill_id: str
    title: str
    trigger_description: str
    body: str
    children: list[SkillSection]
    references: list[SkillFile] = []
    scripts: list[SkillScript] = []
    assets: list[SkillFile] = []
