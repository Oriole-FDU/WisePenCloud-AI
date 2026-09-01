"""HTTP 查询 endpoint 共享的可信权限上下文转换。"""

from common.security import SecurityContextHolder

from rag_v3.domain.acl import PermissionScope


def permission_scope(user_id: str) -> PermissionScope:
    """只从网关写入的上下文构造 ACL 范围，绝不消费客户端请求字段。"""
    return PermissionScope.from_group_roles(
        user_id,
        SecurityContextHolder.get_group_role_map(),
    )
