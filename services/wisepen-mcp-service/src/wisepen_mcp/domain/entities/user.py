from enum import Enum
from typing import Any

from pydantic import Field

from wisepen_mcp.domain.entities.base import ApiModel


class WalletTransactionType(str, Enum):
    REFILL = "REFILL"
    SPEND = "SPEND"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    INCOME = "INCOME"
    EXCHANGE = "EXCHANGE"
    REVERSE = "REVERSE"
    GIFT = "GIFT"
    ONLY_RECORD_META = "ONLY_RECORD_META"


class WalletBusinessType(str, Enum):
    TOKEN = "TOKEN"
    COIN = "COIN"


class MessageType(str, Enum):
    SYSTEM = "SYSTEM"
    NORMAL = "NORMAL"
    RESOURCE_INTERACTION = "RESOURCE_INTERACTION"


class MessageDeliveryScope(str, Enum):
    DIRECT = "DIRECT"
    ALL_USERS = "ALL_USERS"


class UserTaskCode(str, Enum):
    DAILY_CHECK_IN = "DAILY_CHECK_IN"
    ONBOARDING_TUTORIAL = "ONBOARDING_TUTORIAL"
    STUDENT_VERIFICATION = "STUDENT_VERIFICATION"
    TEACHER_VERIFICATION = "TEACHER_VERIFICATION"
    INVITE_VERIFIED_USER = "INVITE_VERIFIED_USER"


class UserTaskType(str, Enum):
    ONCE = "ONCE"
    PERIODIC = "PERIODIC"
    UNCHECKED = "UNCHECKED"


class UserDetailInfo(ApiModel):
    user_id: str = ""
    user_info: dict[str, Any] | None = None
    user_profile: dict[str, Any] | None = None
    readonly_fields: list[str] = Field(default_factory=list)


class WalletDetail(ApiModel):
    token_used: int | None = None
    token_balance: int | None = None
    coin_balance: int | None = None


class WalletTransactionRecord(ApiModel):
    trace_id: str = ""
    operator_id: str = ""
    count: int | None = None
    wallet_transaction_type: WalletTransactionType | None = None
    wallet_business_type: WalletBusinessType | None = None
    meta: str | None = None
    billing_detail: str | None = None
    operator_display: dict[str, Any] | None = None
    create_time: Any = None


class MessageInfo(ApiModel):
    message_id: str = ""
    delivery_scope: MessageDeliveryScope | None = None
    message_type: MessageType | None = None
    title: str = ""
    content: str = ""
    jump_url: str = ""
    extra: str | None = None
    read_time: Any = None
    create_time: Any = None


class UserTaskStatus(ApiModel):
    task_code: UserTaskCode | None = None
    task_type: UserTaskType | None = None
    enabled: bool | None = None
    can_complete: bool | None = None
    reward_preview: dict[str, Any] | None = None
    once: dict[str, Any] | None = None
    periodic: dict[str, Any] | None = None


__all__ = [
    "MessageDeliveryScope",
    "MessageInfo",
    "MessageType",
    "UserDetailInfo",
    "UserTaskCode",
    "UserTaskStatus",
    "UserTaskType",
    "WalletBusinessType",
    "WalletDetail",
    "WalletTransactionRecord",
    "WalletTransactionType",
]
