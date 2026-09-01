"""混合检索 HTTP 输入与输出。"""

from typing import Annotated

from common.utils.ranking import RankDecision
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SearchHybridRequest(BaseModel):
    """混合检索传输参数；用户与 ACL 均由可信请求上下文提供。"""

    model_config = ConfigDict(extra="forbid")

    semantic_query: NonEmptyText
    lexical_query: str = ""
    top_k: int = Field(gt=0)


class ChunkHitResponse(BaseModel):
    """最终精排命中的检索原子及其可继续探索的图节点 ID。"""

    model_config = ConfigDict(from_attributes=True)

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    rerank_score: float
    node_ids: list[str]


class DynamicParentResponse(BaseModel):
    """可直接阅读的连续正文；坐标仍是 application 内部事实。"""

    model_config = ConfigDict(from_attributes=True)

    parent_id: str
    resource_id: str
    content_revision: str
    section_ids: list[str]
    text: str
    matched_chunk_ids: list[str]
    score: float


class SearchHybridResponse(BaseModel):
    """混合检索结果，不附带 Qdrant payload 或 Python 字符坐标。"""

    model_config = ConfigDict(from_attributes=True)

    relevance_decision: RankDecision
    hits: list[ChunkHitResponse]
    parents: list[DynamicParentResponse]
