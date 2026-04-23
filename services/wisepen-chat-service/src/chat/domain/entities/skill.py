from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List
from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel, ASCENDING


class SkillAssetMeta(BaseModel):
    """
    Skill Bundle 内单个附件的元信息
    仅描述"有哪些文件 / 什么用途 / 什么类型"，不存储文件
    实际正文由 SkillAssetLoader.load_asset(skill_id, version, path) 按需懒加载
    """
    path: str = Field(..., description="文件路径")
    kind: str = Field(..., description="资产类型：reference / template / script / example / other")
    description: str = Field(default="", description="对作者和 LLM 友好的简短说明，出现在 assets_manifest 里给模型看")
    size_bytes: int = Field(default=0, description="快照时记录的文件大小，便于治理与审计")


class Skill(Document):
    """
    已发布的 Skill 快照
    """

    # MongoDB _id 直接用 skill_id（slug，机器标识），全链路只用这一个值
    skill_id: str = Field(..., description="唯一 slug，如 'paper-translation'；工具参数、日志、权限校验都只用这个")
    display_name: str = Field(..., description="展示名，如 'Paper Translation'，仅用于日志和可能的 UI 列表")
    description: str = Field(..., description="一句话说明本 Skill 的场景与目的")
    triggers: List[str] = Field(default_factory=list, description="关键词列表，供 KeywordSkillMatcher 大小写无关 substring 匹配")

    skill_md: str = Field(..., description="发布时 SKILL.md 正文快照（含 frontmatter 去掉 --- 后的 body）")
    assets_manifest: List[SkillAssetMeta] = Field(default_factory=list, description="附件清单，LLM 看得到的只有这一层")

    version: str = Field(..., description="发布版本号；资产寻址走 (skill_id, version, path) 三元组")
    enabled: bool = Field(default=True, description="启停开关；False 时 matcher 不会召回")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_published_skill"
        indexes = [
            # skill_id 作为业务主键，需保证唯一；查询也都走它
            IndexModel([("skill_id", ASCENDING)], unique=True),
            # matcher warmup 只拉 enabled=True 的；triggers 未来可能建多键索引，暂不建以省空间
            IndexModel([("enabled", ASCENDING)]),
        ]


@dataclass(frozen=True)
class SkillMeta:
    """
    Matcher / Coordinator 用的轻量元信息快照。
    """
    skill_id: str
    display_name: str
    description: str
    triggers: List[str]
    version: str
