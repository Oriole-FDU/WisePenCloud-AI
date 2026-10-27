"""供应商适配器用于标记已知外部 I/O 失败的最小异常类型。"""


class ProviderError(RuntimeError):
    """保留外部异常文本；适配器应使用原异常作为 cause。"""


def provider_error_message(error: Exception) -> str:
    """HTTPX 超时文本常省略 URL，补齐定位信息但不读取请求头。"""
    message = str(error)
    request = getattr(error, "request", None)
    if request is not None:
        url = str(request.url)
        if url not in message:
            return f"{message} for url '{url}'"
    return message
