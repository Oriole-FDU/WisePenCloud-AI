import json
from contextvars import ContextVar
from typing import Dict, Any, Optional

from common.core.domain import IdentityType, GroupRoleType
from .exceptions import PermissionException, PermissionErrorCode

_security_context: ContextVar[Dict[str, Any]] = ContextVar("security_context", default={})


class SecurityContextHolder:

    @staticmethod
    def _set(key: str, value: Any):
        # 必须先获取现有的 dict 拷贝，再设值，保证请求之间的隔离性
        ctx = _security_context.get().copy()
        ctx[key] = value
        _security_context.set(ctx)

    @staticmethod
    def _get(key: str) -> Optional[Any]:
        return _security_context.get().get(key)

    @staticmethod
    def set_user_id(user_id: str):
        SecurityContextHolder._set("user_id", user_id)

    @staticmethod
    def get_user_id() -> Optional[str]:
        return SecurityContextHolder._get("user_id")

    @staticmethod
    def set_identity_type(code: int):
        """从网关透传的 int code 转换为 IdentityType 枚举存储。"""
        identity = IdentityType.get_by_code(code)
        SecurityContextHolder._set("identity_type", identity)

    @staticmethod
    def get_identity_type() -> Optional[IdentityType]:
        return SecurityContextHolder._get("identity_type")

    @staticmethod
    def set_group_role_map(json_str: str):
        """将 JSON 字符串反序列化，code → GroupRoleType 枚举，存入上下文。"""
        if not json_str:
            return
        try:
            raw: Dict[str, int] = json.loads(json_str)
            role_map: Dict[str, GroupRoleType] = {
                group_id: GroupRoleType.get_by_code(int(code))
                for group_id, code in raw.items()
            }
            SecurityContextHolder._set("group_role_map", role_map)
        except Exception:
            pass

    @staticmethod
    def get_group_role_map() -> Dict[str, GroupRoleType]:
        return SecurityContextHolder._get("group_role_map") or {}

    @staticmethod
    def get_group_role(group_id: str) -> GroupRoleType:
        if not group_id:
            return GroupRoleType.NOT_MEMBER
        return SecurityContextHolder.get_group_role_map().get(group_id, GroupRoleType.NOT_MEMBER)

    # assert 系列：校验失败直接 raise PermissionException

    @staticmethod
    def assert_user_id(user_id: str):
        """校验当前登录用户与目标 user_id 一致，否则抛出越权异常。"""
        if user_id != SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.OPERATION_UNAUTHORIZED)

    @staticmethod
    def assert_in_group(group_id: str) -> GroupRoleType:
        """校验当前用户是目标 Group 的成员，返回其角色；否则抛出越权异常。"""
        role = SecurityContextHolder.get_group_role(group_id)
        if role == GroupRoleType.NOT_MEMBER:
            raise PermissionException(PermissionErrorCode.OPERATION_UNAUTHORIZED)
        return role

    @staticmethod
    def assert_group_role(group_id: str, *required_roles: GroupRoleType):
        """校验当前用户在目标 Group 中的角色在 required_roles 列表内，否则抛出越权异常。"""
        if not group_id or not required_roles:
            raise PermissionException(PermissionErrorCode.OPERATION_UNAUTHORIZED)
        current_role = SecurityContextHolder.get_group_role(group_id)
        if current_role not in required_roles:
            raise PermissionException(PermissionErrorCode.OPERATION_UNAUTHORIZED)