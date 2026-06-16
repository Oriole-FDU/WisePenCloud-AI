from __future__ import annotations

from typing import Any

from chat.application.tools.common.tool_content_store.models import ToolContentReceipt, ToolContentRole
from chat.application.tools.common.tool_content_store.store import ToolContentStore
from chat.application.tools.core.definition import ToolDefinition
from chat.application.tools.core.llm.renderer import RenderToolResult
from chat.application.tools.tool_output_renderer import RenderedToolOutput, render_tool_xml


class ToolOutputCache:
    """工具输出缓存器，只治理 ToolReturn.cacheable_texts。"""

    __slots__ = ("_content_store", "_inline_max_chars")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
        inline_max_chars: int,
    ) -> None:
        self._content_store = content_store
        self._inline_max_chars = inline_max_chars

    async def process_rendered(
        self,
        *,
        rendered: RenderedToolOutput,
        tool_definition: ToolDefinition | None,
        context: dict[str, Any],
    ) -> RenderToolResult:
        """按 ToolReturn.cacheable_texts 生成 inline contents 或 content receipt。"""
        model_text = rendered.rendered_text
        cacheable_texts = tuple(text for text in rendered.cacheable_texts if text)

        if cacheable_texts:
            if sum(len(text) for text in cacheable_texts) <= self._inline_max_chars:
                model_text = render_tool_xml(
                    root_tag=rendered.root_tag,
                    payload=rendered.visible_result,
                    inline_contents=cacheable_texts,
                )
            else:
                receipts = []
                for index, text in enumerate(cacheable_texts):
                    receipt = await self._content_store.put(
                        session_id=context["session_id"],
                        producer=rendered.tool_name,
                        source=f"tool_call:{rendered.tool_call_id}:cacheable_text:{index}",
                        text=text,
                        content_type="text/markdown",
                        content_role=ToolContentRole.TOOL_OUTPUT,
                        metadata={
                            "tool": rendered.tool_name,
                            "tool_call_id": rendered.tool_call_id,
                            "tool_arguments": rendered.tool_arguments,
                            "cache_payload": "cacheable_texts",
                            "cacheable_text_index": index,
                            "cacheable_text_count": len(cacheable_texts),
                        },
                        chunked=tool_definition.policy.cache_chunked if tool_definition is not None else True,
                    )
                    if receipt is not None:
                        receipts.append(receipt)

                if receipts:
                    model_text = render_tool_xml(
                        root_tag=rendered.root_tag,
                        payload=rendered.visible_result,
                        content_receipts=tuple(_content_receipt_payload(receipt) for receipt in receipts),
                    )

        persisted_output_placeholder = None
        if tool_definition is not None and not tool_definition.policy.persist_output:
            try:
                persisted_output_placeholder = tool_definition.policy.persisted_output_placeholder_factory(
                    rendered.tool_arguments,
                    model_text,
                )
            except Exception:
                persisted_output_placeholder = None
            persisted_output_placeholder = persisted_output_placeholder or "[Tool output persisted.]"

        return RenderToolResult(
            tool_call_id=rendered.tool_call_id,
            tool_name=rendered.tool_name,
            persisted_output_placeholder=persisted_output_placeholder,
            tool_output=model_text,
        )


def _content_receipt_payload(receipt: ToolContentReceipt) -> dict[str, Any]:
    return {
        "content_id": receipt.content_id,
        "read_action": "tool_content_read",
        "content_role": receipt.content_role,
        "content_type": receipt.content_type,
        "original_length": receipt.original_length,
        "chunk_count": receipt.chunk_count,
        "read_modes": list(receipt.read_modes),
        "selectors": list(receipt.selectors),
    }
