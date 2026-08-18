"""Neo4j 图谱 ACL 同步仓储契约。"""

from typing import Protocol

from rag.domain.models.acl import ResourceAcl


class GraphAclWriter(Protocol):
    async def initialize(self) -> None: ...

    async def synchronize(self, resource_acl: ResourceAcl) -> None: ...
