"""混合检索 HTTP 输入与输出。"""

from typing import Annotated

from common.utils.ranking import RankDecision
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SearchHybridRequest(BaseModel):
    """混合检索传输参数；用户与 ACL 均由可信请求上下文提供。"""

    model_config = ConfigDict(extra="forbid")

    semantic_query: NonEmptyText = Field(description="用于语义检索的查询文本。")
    lexical_query: str = Field(default="", description="可选的关键词检索文本。")
    top_k: int = Field(gt=0, description="最多返回的检索父块数量。")


class DynamicParentResponse(BaseModel):
    """模型可直接消费的连续正文及其最小定位信息。"""

    model_config = ConfigDict(from_attributes=True)

    resource_id: str = Field(description="父块所属资源标识。")
    section_id: str | None = Field(description="父块所属的 Section ID；无标题正文时为空。")
    section_path: str = Field(description="父块所属的标题路径，以 ` > ` 连接。")
    text: str = Field(description="可直接提供给模型阅读的连续正文。")
    score: float = Field(description="该父块的相关性分数。")


class SearchHybridResponse(BaseModel):
    """混合检索结果，只保留模型消费所需的相关性和阅读父块。"""

    model_config = ConfigDict(from_attributes=True)

    relevance_decision: RankDecision = Field(description="整体相关性门控结果。")
    parents: list[DynamicParentResponse] = Field(description="模型可直接消费的动态父块。")
