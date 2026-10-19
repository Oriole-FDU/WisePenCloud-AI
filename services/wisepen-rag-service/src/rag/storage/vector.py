"""向量存储的投影值对象和读写端口，不选择 revision 或解释 ACL。"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from rag.core.schema.filters import MetadataFilters


class IndexKind(StrEnum):
    """后端无关的 metadata 索引类型；时间由业务转换为整数时间戳。"""

    KEYWORD = "keyword"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class IndexableField:
    """一个平面 metadata 字段的索引声明，不携带数据库 SDK 类型。"""

    name: str  # metadata 内的相对字段名称。
    kind: IndexKind


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


class VectorReader(Protocol):
    async def query(self, request: VectorQuery) -> list[VectorMatch]:
        """按 provider 排名返回候选；filters=None 表示不限制 metadata。"""
        ...


class VectorWriter(Protocol):
    async def upsert(self, records: list[VectorRecord]) -> None:
        """全部写入确认后返回；空批量无 I/O，部分成功不补偿。"""
        ...

    async def delete(self, filters: MetadataFilters) -> None:
        """只接受非空删除条件，完成后返回；撤销可见性由调用方负责。"""
        ...


class VectorStorage(VectorReader, VectorWriter, Protocol):
    """同一 adapter 组合读写能力，collection 绑定实例而非每条记录。"""


@dataclass(frozen=True, slots=True)
class VectorCollection:
    """装配层的集合及索引配置，启动初始化消费；GC 仅使用其中的 storage。"""

    storage: VectorStorage
    metadata_index_fields: list[IndexableField]
