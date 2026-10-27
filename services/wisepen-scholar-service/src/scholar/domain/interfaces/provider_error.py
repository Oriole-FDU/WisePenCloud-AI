"""供应商适配器用于标记已知外部 I/O 失败的最小异常类型。"""


class ProviderError(RuntimeError):
    """保留外部异常文本；适配器应使用原异常作为 cause。"""


class ProviderBusy(ProviderError):
    """来源准入拒绝，尚未发送 HTTP；允许普通通用来源回退。"""

    def __init__(self, queued_ahead: int, limit: int):
        self.queued_ahead = queued_ahead
        super().__init__(
            f"S2 queue busy: {queued_ahead} waiting ahead exceeds limit {limit}"
        )


def provider_error_message(error: Exception) -> str:
    """HTTPX 超时文本常省略 URL，补齐定位信息但不读取请求头。"""
    message = str(error)
    request = getattr(error, "request", None)
    if request is not None:
        url = str(request.url)
        if url not in message:
            return f"{message} for url '{url}'"
    return message
