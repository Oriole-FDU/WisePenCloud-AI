"""本地 ACL 投影仓储端口。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from .models import ResourceAcl


class ResourceAclRepository(Protocol):
    """RAG 在线判权使用的本地 ACL 副本。"""

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]: ...

    async def save(self, resource_acl: ResourceAcl) -> bool:
         ...

    async def delete(self, resource_id: str) -> None: 
        ...