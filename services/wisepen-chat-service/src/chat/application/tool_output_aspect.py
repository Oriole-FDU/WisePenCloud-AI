from chat.application.infra.content_store.formatting import format_tool_content_window
from chat.application.tools.tool_content_store import ToolContentStore
from chat.core.config.app_settings import settings

_TOOL_ERROR_PREFIXES = (
    "[Tool Error]",
    "[Tool Execution Error]",
)


class ToolOutputAspect:
    """在工具结果进入流式事件和会话历史前做统一后处理。"""

    def __init__(self, *, content_store: ToolContentStore) -> None:
        """初始化工具结果切面。

        Args:
            content_store: 由容器注入的 ToolContentStore。
        """
        self._content_store = content_store

    def process(
        self,
        *,
        session_id: str,
        tool_name: str,
        output: str,
        should_output_receipt: bool = False,
    ) -> str:
        """缓存超过阈值的工具输出，并返回模型可见的首个窗口。

        Args:
            session_id: 当前会话 ID，用于内容作用域隔离。
            tool_name: 产生该输出的工具名。
            output: 原始工具输出。

        Returns:
            未超过阈值时返回原文；超过阈值时返回 ToolContent 首窗口。
        """
        if not self._should_cache(output):
            return output

        # 缓存全文并立即读取引导信息
        window = self._content_store.put_and_read_window(
            session_id=session_id,
            tool_name=tool_name,
            source=f"tool:{tool_name}",
            text=output,
            content_type="text/plain",
            metadata={
                "content_kind": "tool_output",
                "tool_name": tool_name,
                "cache_policy": "tool_result_max_chars",
            },
            offset=0,
            limit=settings.TOOL_RESULT_MAX_CHARS,
        )
        # 格式化为 AI 友好的上下文
        return format_tool_content_window(window)

    @staticmethod
    def _should_cache(output: str) -> bool:
        # 未达到阈值，不需要缓存
        if len(output) <= settings.TOOL_RESULT_MAX_CHARS:
            return False
        # 异常信息不做缓存
        if output.startswith(_TOOL_ERROR_PREFIXES):
            return False
        # 元信息不需要缓存
        if output.startswith("[ToolContent Metadata]") or output.startswith(
            "[ToolContent Receipt]"
        ):
            return False
        return True


