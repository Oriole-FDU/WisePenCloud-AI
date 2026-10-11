"""摄入与检索共享的向量投影合同；正文事实仍属于 Document 和 Chunk。"""

from dataclasses import dataclass, field

from .filters import MetadataFilters


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """统一投影输入；text 用于 sparse 构建，不要求后端保存正文。"""

    record_id: str  # 业务原始身份，后端 point ID 映射不改变它。
    text: str
    embedding: list[float]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorQuery:
    """一次独立召回，embedding 与 text 必须且只能提供一个；融合由上层负责。"""

    embedding: list[float] | None = None
    text: str | None = None
    top_k: int = 10
    filters: MetadataFilters | None = None
    include_embedding: bool = False  # 多样化可请求命中的已存 dense 向量。


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """候选定位与 provider 分数；不携带正文或授权结论。"""

    record_id: str
    score: float
    metadata: dict[str, object] = field(default_factory=dict)
    embedding: list[float] | None = None  # 默认不加载；不对候选正文重新 embedding。
