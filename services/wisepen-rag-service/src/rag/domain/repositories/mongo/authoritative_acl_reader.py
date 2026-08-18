"""上游资源库权威 ACL 的读取契约。"""

from typing import Protocol

from rag.domain.models.acl import ResourceAcl


class AuthoritativeAclReader(Protocol):
    """从上游资源集合读取单个资源的完整 ACL 事实。"""

    async def get_resource_acl(self, resource_id: str) -> ResourceAcl | None: ...
