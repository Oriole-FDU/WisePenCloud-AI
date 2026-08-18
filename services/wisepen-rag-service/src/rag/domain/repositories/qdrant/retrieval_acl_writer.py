"""检索索引 ACL 同步仓储契约。"""

from typing import Protocol

from rag.domain.models.acl import ResourceAcl


class RetrievalAclWriter(Protocol):
    async def synchronize(self, resource_acl: ResourceAcl) -> None: ...
