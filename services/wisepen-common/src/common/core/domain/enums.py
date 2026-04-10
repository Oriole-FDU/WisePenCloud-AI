from enum import Enum
from typing import Optional
from common.core.utils import auto_enum



class IErrorCode(Enum):
    @property
    def code(self) -> int:
        return self.value[0]

    @property
    def msg(self) -> str:
        return self.value[1]

class ResultCode(IErrorCode):
    SUCCESS = (200, "操作成功")
    SYSTEM_ERROR = (500, "系统内部错误")
    PARAM_ERROR = (400, "参数验证失败")


@auto_enum("code", "desc")
class IdentityType(Enum):
    STUDENT = (1, "STUDENT")
    TEACHER = (2, "TEACHER")
    ADMIN = (3, "ADMIN")

    def __init__(self, code: int, desc: str):
        self.code = code
        self.desc = desc


@auto_enum("code", "desc")
class GroupRoleType(Enum):
    OWNER = (0, "OWNER")
    ADMIN = (1, "ADMIN")
    MEMBER = (2, "MEMBER")
    NOT_MEMBER = (-1, "NOT_MEMBER")



