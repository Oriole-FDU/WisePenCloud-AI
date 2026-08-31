"""ACL application 用例：读取资源 ACL 并执行 VIEW 授权。"""

from collections.abc import Iterable

from rag.domain.models.acl import PermissionScope
from rag.domain.repositories.mongo.resource_acl_store import ResourceAclStore


class PermissionAuthorizer:
    """以本地 ACL 事实作为 RAG 读取前的最终授权门。"""

    __slots__ = ("_local_store",)

    def __init__(self, *, local_store: ResourceAclStore) -> None:
        self._local_store = local_store

    async def authorize_resource(
        self,
        *,
        resource_id: str,
        scope: PermissionScope,
    ) -> bool:
        """判断单个资源是否可读；ACL 缺失时 fail closed。"""
        resource_acl = await self._local_store.get_resource_acl(resource_id)
        return resource_acl is not None and resource_acl.can_read(scope)

    async def readable_resource_ids(
        self,
        resource_ids: Iterable[str],
        *,
        scope: PermissionScope,
    ) -> list[str]:
        """按输入顺序返回当前用户可读的资源 ID。

        只返回本地 ACL 中存在且通过授权的资源；未命中不回源、不默认放行，
        这样批量召回天然保持 fail-closed。
        """
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return []

        resource_acls = await self._local_store.get_resource_acls(unique_resource_ids)
        return [
            resource_id
            for resource_id in unique_resource_ids
            if (resource_acl := resource_acls.get(resource_id)) is not None
            and resource_acl.can_read(scope)
        ]
