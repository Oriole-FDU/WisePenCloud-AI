"""检索领域枚举。"""

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
