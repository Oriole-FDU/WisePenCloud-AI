"""通用领域到聚合搜索来源的统一映射；映射只是召回近似，不宣称分类体系等价。"""

from scholar.domain.models.fields import AcademicField

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


# OpenAlex 同字段多个 ID 用 OR；general 不添加领域过滤。
OPENALEX_FILTERS: dict[AcademicField, str | None] = {
    AcademicField.GENERAL: None,
    AcademicField.COMPUTER_SCIENCE: "topics.field.id:17",
    AcademicField.MEDICINE: "topics.field.id:27|29|30|35|36",
    AcademicField.BIOLOGY: "topics.field.id:11|13|24|28|34",
    AcademicField.ENGINEERING: "topics.field.id:15|21|22",
    AcademicField.ECONOMICS_BUSINESS: "topics.field.id:14|20",
    AcademicField.MATHEMATICS: "topics.field.id:26|18",
    AcademicField.PHYSICS: "topics.field.id:31",
    AcademicField.CHEMISTRY: "topics.field.id:16",
    AcademicField.PSYCHOLOGY: "topics.field.id:32",
    AcademicField.SOCIAL_SCIENCES: "topics.field.id:33",
    AcademicField.ARTS_HUMANITIES: "topics.field.id:12",
    AcademicField.LAW: "topics.subfield.id:3308",
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: "topics.field.id:19|23|25",
}
