from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RerankableDocument:
    """可重排文档。

    包含需要送入重排器（Reranker）的文档 ID 和文本内容。
    """

    id: str
    text: str


@dataclass(frozen=True, slots=True)
class RerankedDocument:
    """重排后的文档。

    包含重排器返回的文档 ID、得分和排名。
    """

    id: str
    score: float
    rank: int
