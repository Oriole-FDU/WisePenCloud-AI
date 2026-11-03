"""公开结果只描述未恢复的业务限制，异常与阶段信息留在审计日志中。"""

from dataclasses import dataclass
from enum import StrEnum

import httpx
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from redis.exceptions import TimeoutError as RedisTimeout

from scholar.domain.errors import ProviderBusy, ProviderCredentialsRequired, ProviderError


class LimitationReason(StrEnum):
    RATE_LIMITED = "rate_limited"
    CREDENTIALS_REQUIRED = "credentials_required"
    CREDENTIAL_REJECTED = "credential_rejected"
    SOURCE_UNAVAILABLE = "source_unavailable"
    RETRIEVAL_DEGRADED = "retrieval_degraded"


def dependency_status(error: Exception) -> int:
    """按原始异常识别依赖状态；不从供应商文案猜测限流或超时。"""
    while isinstance(error, ProviderError) and error.__cause__ is not None:
        if isinstance(error, ProviderBusy):
            return 429
        error = error.__cause__
    if isinstance(error, ProviderBusy):
        return 429
    if isinstance(error, ResponseHandlingException):
        error = error.source
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    if isinstance(error, UnexpectedResponse):
        return error.status_code
    if isinstance(error, (TimeoutError, httpx.TimeoutException, RedisTimeout)):
        return 504
    return 503


@dataclass(frozen=True)
class ResultLimitation:
    """来源及可行动原因；不保存诊断文本、阶段或执行历史。"""

    source: str
    reason: LimitationReason

    @classmethod
    def from_error(cls, source: str, error: Exception) -> "ResultLimitation":
        status = dependency_status(error)
        if isinstance(error, ProviderCredentialsRequired):
            reason = LimitationReason.CREDENTIALS_REQUIRED
        elif status == 429:
            reason = LimitationReason.RATE_LIMITED
        elif status in (401, 403):
            reason = LimitationReason.CREDENTIAL_REJECTED
        else:
            reason = LimitationReason.SOURCE_UNAVAILABLE
        return cls(source, reason)
