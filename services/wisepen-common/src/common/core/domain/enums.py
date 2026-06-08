from enum import Enum
from typing import Optional

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

class IdentityType(Enum):
    STUDENT = (1, "STUDENT")
    TEACHER = (2, "TEACHER")
    ADMIN = (3, "ADMIN")

    def __init__(self, code: int, desc: str):
        self.code = code
        self.desc = desc

    @classmethod
    def get_by_code(cls, code: Optional[int]) -> Optional["IdentityType"]:
        if code is None:
            return None
        for member in cls:
            if member.code == code:
                return member
        return None


class GroupRoleType(Enum):
    OWNER = (0, "OWNER")
    ADMIN = (1, "ADMIN")
    MEMBER = (2, "MEMBER")
    NOT_MEMBER = (-1, "NOT_MEMBER")

    def __init__(self, code: int, desc: str):
        self.code = code
        self.desc = desc

    @classmethod
    def get_by_code(cls, code: Optional[int]) -> Optional["GroupRoleType"]:
        if code is None:
            return None
        for member in cls:
            if member.code == code:
                return member
        return None

