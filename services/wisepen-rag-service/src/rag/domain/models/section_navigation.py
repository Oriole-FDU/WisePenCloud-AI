"""标题树导航共享的语义模型。"""

from dataclasses import dataclass, field
from enum import StrEnum


class SectionDirection(StrEnum):
    """SectionExpander 支持的四个原子方向。"""

    PARENT = "parent"
    CHILDREN = "children"
    PREVIOUS = "previous"
    NEXT = "next"


@dataclass(slots=True)
class SectionReference:
    """可直接继续阅读的 Section 视图。"""

    section_id: str
    title: str
    section_path: str
    text: str
    allowed_directions: list[SectionDirection] = field(default_factory=list)
