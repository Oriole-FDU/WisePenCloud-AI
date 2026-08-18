"""本地资源 ACL 的写入、读取和删除契约。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from rag.domain.models.acl import ResourceAcl


class ResourceAclStore(Protocol):
    """维护 RAG 侧本地 ACL 事实，不读取上游权威集合。"""

    async def get_resource_acl(self, resource_id: str) -> ResourceAcl | None: ...

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]: ...

    async def save_if_newer(self, resource_acl: ResourceAcl) -> bool: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...
