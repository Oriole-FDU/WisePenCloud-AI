"""将一批完整标题解析为稳定身份候选的外部边界。"""

from dataclasses import dataclass, field
from typing import Protocol

from scholar.domain.models.identifiers import PaperIdentifier


@dataclass
class TitleMatch:
    """一个标题的候选；每个内部列表是同一论文的身份别名，多组表示歧义。"""

    candidates: list[list[PaperIdentifier]] = field(default_factory=list)
    # 保留原始异常及 cause，供用例分类和审计，不能直接序列化给调用方。
    error: Exception | None = None


class TitleResolver(Protocol):
    async def match(self, titles: list[str], *, deadline: float | None = None) -> list[TitleMatch]: ...
