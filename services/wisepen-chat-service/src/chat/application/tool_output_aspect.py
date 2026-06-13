from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chat.application.tools.common.context_bundle import (
    ContextAdapter,
    ContextBundle,
    ModelContextRenderer,
)
from chat.application.tools.common.tool_content_store.models import ToolContentReceipt, ToolContentRole
from chat.application.tools.common.tool_content_store.store import ToolContentStore
from chat.application.tools.core.definition import ToolDefinition
from chat.application.tools.core.execution.result import ToolExecutionResult
from chat.application.tools.core.llm.renderer import RenderToolResult


_TOOL_ERROR_PREFIX = "[Tool Error]"


@dataclass(frozen=True, slots=True)
class ProcessedToolOutput:
    """工具输出统一切面处理结果。"""

    rendered_text: str  # 进入模型 tool message 的文本
    receipt: ToolContentReceipt | None = None  # 渲染结果缓存凭证
    inline: bool = True  # 是否内联给模型


class ToolOutputAspect:
    """统一处理工具输出的格式化和缓存。"""

    __slots__ = ("_content_store", "_adapter", "_renderer", "_inline_max_chars")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
        adapter: ContextAdapter,
        renderer: ModelContextRenderer,
        inline_max_chars: int,
    ) -> None:
        self._content_store = content_store
        self._adapter = adapter
        self._renderer = renderer
        self._inline_max_chars = inline_max_chars

    def process_result(
        self,
        *,
        tool_result: ToolExecutionResult,
        tool_definition: ToolDefinition | None,
        context: dict[str, Any],
    ) -> RenderToolResult:
        """把工具原始输出转换成模型可见输出，并写入 ToolContentStore。"""
        if tool_result.tool_execution_error is not None:
            output = _format_tool_error(tool_result)
            return RenderToolResult(
                tool_call_id=tool_result.tool_invocation.tool_call_id,
                tool_name=tool_result.tool_invocation.tool_name,
                persisted_output_placeholder=None,
                tool_output=output,
            )

        processed = self.process_output(
            session_id=context["session_id"],
            tool_name=tool_result.tool_invocation.tool_name,
            tool_call_id=tool_result.tool_invocation.tool_call_id,
            tool_arguments=tool_result.tool_invocation.tool_call_arguments,
            output=tool_result.tool_output,
        )

        persisted_output_placeholder = None
        if tool_definition is not None and not tool_definition.policy.persist_output:
            try:
                persisted_output_placeholder = tool_definition.policy.persisted_output_placeholder_factory(
                    tool_result.tool_invocation.tool_call_arguments,
                    processed.rendered_text,
                )
            except Exception:
                persisted_output_placeholder = None
            persisted_output_placeholder = persisted_output_placeholder or "[Tool output persisted.]"

        return RenderToolResult(
            tool_call_id=tool_result.tool_invocation.tool_call_id,
            tool_name=tool_result.tool_invocation.tool_name,
            persisted_output_placeholder=persisted_output_placeholder,
            tool_output=processed.rendered_text,
        )

    def process_output(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_call_id: str,
        tool_arguments: dict[str, Any],
        output: str | ContextBundle,
    ) -> ProcessedToolOutput:
        """处理单个工具输出。"""
        bundle = output if isinstance(output, ContextBundle) else self._adapter.from_text(
            output,
            title=tool_name,
            metadata={
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "tool_arguments": tool_arguments,
            },
        )
        rendered_text = self._renderer.render_bundle(bundle)
        receipt = self._content_store.put(
            session_id=session_id,
            producer=tool_name,
            source=f"tool_call:{tool_call_id}",
            text=rendered_text,
            content_type="text/xml",
            content_role=ToolContentRole.MODEL_CONTEXT_RENDERED,
            metadata={
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "tool_arguments": tool_arguments,
                "manifest": bundle.manifest().model_dump(mode="json"),
            },
        )
        if receipt is None:
            # 缓存失败不阻断工具结果。此时没有 content_id 可返回，只能把渲染后的
            # 上下文完整暴露给模型；代价是本次输出无法被后续 tool_content_read 精确读取。
            return ProcessedToolOutput(rendered_text=rendered_text, receipt=None, inline=True)

        if len(rendered_text) <= self._inline_max_chars:
            return ProcessedToolOutput(rendered_text=rendered_text, receipt=receipt, inline=True)

        return ProcessedToolOutput(
            rendered_text=self._render_receipt(tool_name=tool_name, tool_call_id=tool_call_id, receipt=receipt),
            receipt=receipt,
            inline=False,
        )

    def _render_receipt(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        receipt: ToolContentReceipt,
    ) -> str:
        """渲染大输出的模型可见 receipt。"""
        read_modes = ", ".join(receipt.read_modes)
        selectors = ", ".join(receipt.selectors)
        return "\n".join(
            (
                "<tool_content_receipt>",
                f"tool: {tool_name}",
                f"tool_call_id: {tool_call_id}",
                f"content_id: {receipt.content_id}",
                f"content_role: {receipt.content_role}",
                f"content_type: {receipt.content_type}",
                f"original_length: {receipt.original_length}",
                f"chunk_count: {receipt.chunk_count}",
                f"read_modes: {read_modes}",
                f"selectors: {selectors}",
                "reason: Tool output exceeded inline budget and was cached.",
                "</tool_content_receipt>",
            )
        )


def _format_tool_error(tool_result: ToolExecutionResult) -> str:
    error = tool_result.tool_execution_error
    if error is None:
        return _TOOL_ERROR_PREFIX
    output = f"{_TOOL_ERROR_PREFIX} {error.reason}"
    if error.detail_reason:
        output = f"{output}: {error.detail_reason}"
    return output
