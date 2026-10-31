"""领域与供应商错误定义及辅助函数。"""

from __future__ import annotations

from contextvars import ContextVar

from common.core.domain import IErrorCode

# 同一搜索任务记录当前依赖操作；并发来源各自持有上下文，超时后仍能指出中断位置。
provider_operation: ContextVar[str | None] = ContextVar("scholar_provider_operation", default=None)


class ScholarErrorCode(IErrorCode):
    """学者服务通用错误码。"""

    CAPABILITY_UNAVAILABLE = (503, "Requested scholar capability is unavailable")


class ProviderError(RuntimeError):
    """供应商适配器已知外部 I/O 失败的最小异常类型。

    保留外部异常文本；适配器应使用原异常作为 cause。
    """


class ProviderBusy(ProviderError):
    """来源准入拒绝（如排队数超限），尚未发送 HTTP；允许普通通用来源回退。"""

    def __init__(self, queued_ahead: int, limit: int):
        self.queued_ahead = queued_ahead
        super().__init__(
            f"S2 queue busy: {queued_ahead} waiting ahead exceeds limit {limit}"
        )


def provider_error_message(error: Exception) -> str:
    """HTTPX / Elastic 等异常文本格式化。

    超时或连接失败常省略 URL，补齐定位信息但不读取请求头中的敏感凭证。
    """
    message = str(error) or type(error).__name__
    request = getattr(error, "request", None)
    if request is not None:
        url = str(request.url)
        if url not in message:
            return f"{message} for url '{url}'"
    return message
