"""绑定 collection 的向量读写端口，不生成文本、不选择 revision 或解释 ACL。"""

from typing import Protocol

from rag.core.schema.filters import MetadataFilters
from rag.core.schema.vector import VectorMatch, VectorQuery, VectorRecord


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
