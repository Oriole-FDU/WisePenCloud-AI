from __future__ import annotations

from typing import Any

from common.logger import warn

from chat.application.tools.common.tool_content_store.models import ToolContentReceipt, ToolContentRole
from chat.application.tools.common.tool_content_store.store import ToolContentStore
from chat.application.tools.core.definition import ToolDefinition
from chat.application.tools.core.llm.renderer import RenderToolResult
from chat.application.tools.tool_output_renderer import RenderedToolOutput, render_tool_xml


class ToolOutputCache:
    """工具输出缓存器，专门负责对 ToolReturn.cacheable_texts 进行内联或存仓治理。"""

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
        """根据文本包大小，动态将 cacheable_texts 渲染为内联 XML 文本或持久化内容回执。"""
        model_text = rendered.rendered_text
        cacheable_texts = tuple(text for text in rendered.cacheable_texts if text)

        # 1. 动态评估治理：判断是否携带需要处理的富文本内容块
        if cacheable_texts:
            total_chars = sum(len(text) for text in cacheable_texts)

            # 分支 A: 文本总量在安全窗口内 -> 直接内联嵌入 CDATA 块
            if total_chars <= self._inline_max_chars:
                model_text = render_tool_xml(
                    root_tag=rendered.root_tag,
                    payload=rendered.visible_result,
                    inline_contents=cacheable_texts,
                )

            # 分支 B: 文本量超限 -> 降级驱动存仓，仅返回轻量化的 XML 引用凭证
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
                        chunked=(
                            tool_definition.policy.cache_chunked
                            if tool_definition is not None
                            else True
                        ),
                    )
                    if receipt is not None:
                        receipts.append(receipt)
                    else:
                        warn(
                            "tool output cache receipt missing.",
                            tool_name=rendered.tool_name,
                            tool_call_id=rendered.tool_call_id,
                            cacheable_text_index=index,
                            audit_message="工具输出内容仓库写入未返回 receipt，该文本不会出现在模型输出凭证中。",
                        )

                # 只要有一条单据存仓成功，就重构重写发送给模型的 XML 树
                if receipts:
                    receipt_payloads = tuple(
                        _content_receipt_payload(r) for r in receipts
                    )
                    model_text = render_tool_xml(
                        root_tag=rendered.root_tag,
                        payload=rendered.visible_result,
                        content_receipts=receipt_payloads,
                    )

        # 2. 持久化输出裁剪：若配置了不持久化输出，生成动态文本占位符预防膨胀
        persisted_output_placeholder = None
        if tool_definition is not None and not tool_definition.policy.persist_output:
            try:
                persisted_output_placeholder = (
                    tool_definition.policy.persisted_output_placeholder_factory(
                        rendered.tool_arguments,
                        model_text,
                    )
                )
            except Exception as exc:
                warn(
                    "tool output placeholder factory failed.",
                    e=exc,
                    tool_name=rendered.tool_name,
                    tool_call_id=rendered.tool_call_id,
                    audit_message="工具输出持久化占位工厂执行失败，已回退到默认占位文本。",
                )
                persisted_output_placeholder = None

            persisted_output_placeholder = (
                    persisted_output_placeholder or "[Tool output persisted.]"
            )

        return RenderToolResult(
            tool_call_id=rendered.tool_call_id,
            tool_name=rendered.tool_name,
            persisted_output_placeholder=persisted_output_placeholder,
            tool_output=model_text,
        )


def _content_receipt_payload(receipt: ToolContentReceipt) -> dict[str, Any]:
    """将底层的 ToolContentReceipt 核心实体拍平为对模型可见的 XML 清晰字典载荷。"""
    return {
        "content_id": receipt.content_id,
        "content_role": receipt.content_role,
        "content_type": receipt.content_type,
        "original_length": receipt.original_length,
        "chunk_count": receipt.chunk_count,
        "selectors": list(receipt.selectors),
    }
