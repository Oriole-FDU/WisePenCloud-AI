"""通用领域到聚合搜索来源的统一映射；映射只是召回近似，不宣称分类体系等价。"""

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


ARXIV_CATEGORIES: dict[AcademicField, list[str] | None] = {
    AcademicField.GENERAL: [],
    AcademicField.COMPUTER_SCIENCE: ["cs.*"],
    AcademicField.MEDICINE: None,
    AcademicField.BIOLOGY: ["q-bio.*"],
    AcademicField.ENGINEERING: ["eess.*"],
    AcademicField.ECONOMICS_BUSINESS: ["econ.*", "q-fin.*"],
    AcademicField.MATHEMATICS: ["math.*", "stat.*"],
    AcademicField.PHYSICS: [
        "physics.*",
        "astro-ph.*",
        "cond-mat.*",
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "math-ph",
        "nlin.*",
        "nucl-ex",
        "nucl-th",
        "quant-ph",
    ],
    AcademicField.CHEMISTRY: ["physics.chem-ph"],
    AcademicField.PSYCHOLOGY: None,
    AcademicField.SOCIAL_SCIENCES: ["physics.soc-ph"],
    AcademicField.ARTS_HUMANITIES: None,
    AcademicField.LAW: None,
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: [
        "cond-mat.mtrl-sci",
        "physics.ao-ph",
        "physics.geo-ph",
    ],
}
