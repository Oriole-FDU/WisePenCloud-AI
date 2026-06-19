from common.core.domain import IErrorCode
from common.core.exceptions import ServiceException


class PermissionException(ServiceException):
    """权限相关异常"""
    pass


class PermissionErrorCode(IErrorCode):
    NOT_LOGIN = (401, "未登录")
    UNAUTHORIZED = (401, "未授权")
    IDENTITY_UNAUTHORIZED = (403, "当前身份角色不满足业务要求")
    OPERATION_UNAUTHORIZED = (403, "业务操作权限不足")
    RESOURCE_UNAUTHORIZED = (403, "资源访问权限不足")
