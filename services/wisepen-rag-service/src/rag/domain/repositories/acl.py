"""ACL 来源与本地副本的仓储端口。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from rag.domain.acl import ResourceAcl


class AuthoritativeAclReader(Protocol):
    """只读上游 `wispen_resource_items` 的 ACL 来源事实。"""

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]: ...


class ResourceAclRepository(Protocol):
    """RAG 在线判权使用的本地 ACL 副本。"""

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]: ...

    async def save_if_newer(self, resource_acl: ResourceAcl) -> bool: ...
