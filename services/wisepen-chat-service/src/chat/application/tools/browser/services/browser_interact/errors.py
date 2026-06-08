from chat.application.tools.browser.services.browser_interact.enums import DiagnosticCode


class BrowserInteractError(RuntimeError):
    """browser_interact 服务错误基类。"""


class BrowserSessionError(BrowserInteractError):
    """记录浏览器会话启动和生命周期异常。"""

    def __init__(
        self,
        message: str,
        diagnostic_code: str = DiagnosticCode.BROWSER_SESSION_ERROR.value,
    ) -> None:
        """初始化带稳定诊断码的浏览器会话异常。

        Args:
            message: 面向日志和响应的异常说明。
            diagnostic_code: 稳定诊断码，用于上层构建结构化错误响应。
        """
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
