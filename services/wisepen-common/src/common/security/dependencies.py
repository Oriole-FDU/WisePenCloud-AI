from typing import Callable
from .context import SecurityContextHolder
from .exceptions import PermissionException, PermissionErrorCode
from common.core.domain import IdentityType


def require_login() -> str:
    """
    校验当前请求已登录，返回 user_id；否则抛出 PermissionException。
    用法：user_id: str = Depends(require_login)
    """
    user_id = SecurityContextHolder.get_user_id()
    if not user_id:
        raise PermissionException(PermissionErrorCode.NOT_LOGIN)
    return user_id


def require_role(*roles: IdentityType) -> Callable:
    """
    工厂函数，返回可用于 Depends 的校验函数。
    同时隐含登录校验。
    用法：_ = Depends(require_role(IdentityType.ADMIN, IdentityType.TEACHER))
    """
    def _check() -> str:
        user_id = require_login()
        current = SecurityContextHolder.get_identity_type()
        if current is None or current not in roles:
            raise PermissionException(PermissionErrorCode.IDENTITY_UNAUTHORIZED)
        return user_id
    return _check
