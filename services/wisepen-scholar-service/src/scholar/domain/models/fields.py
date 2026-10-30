"""学术领域及对应的能力策略。"""

from dataclasses import dataclass, field
from enum import StrEnum


class AcademicField(StrEnum):
    GENERAL = "general"
    COMPUTER_SCIENCE = "computer_science"
    MEDICINE = "medicine"
    BIOLOGY = "biology"
    ENGINEERING = "engineering"
    ECONOMICS_BUSINESS = "economics_business"
    MATHEMATICS = "mathematics"
    PHYSICS = "physics"
    CHEMISTRY = "chemistry"
    PSYCHOLOGY = "psychology"
    SOCIAL_SCIENCES = "social_sciences"
    ARTS_HUMANITIES = "arts_humanities"
    LAW = "law"
    MATERIALS_ENVIRONMENTAL_SCIENCES = "materials_environmental_sciences"


@dataclass
class FieldPolicy:
    """单个领域的有序检索来源、增量富化来源与可选发现来源。"""

    searchers: list[str]
    # 各 provider 保证命名空间唯一且提供增量；前面来源成功不会跳过后面的来源。
    field_enrichers: list[str] = field(default_factory=list)
    discoverer: str | None = None


@dataclass
class ScholarPolicies:
    """完整策略快照；通用富化顺序和领域策略一起发布，发布后不再修改。"""

    common_enrichers: list[str]
    fields: dict[AcademicField, FieldPolicy]
