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


class DynamicParentResponse(BaseModel):
    """模型可直接消费的连续正文及其最小定位信息。"""

    model_config = ConfigDict(from_attributes=True)

    resource_id: str
    section_id: str | None
    text: str
    score: float


class SearchHybridResponse(BaseModel):
    """混合检索结果，只保留模型消费所需的相关性和阅读父块。"""

    model_config = ConfigDict(from_attributes=True)

    relevance_decision: RankDecision
    parents: list[DynamicParentResponse]
