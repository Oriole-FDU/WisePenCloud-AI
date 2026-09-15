from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult

from chat.application.tools.core import (
    Tool,
    ToolDefinition,
    ToolExecutionError,
    ToolOutput,
)
from chat.application.tools.core.llm.renderer import normalize_json_value
from chat.application.tools.core.output_cache import process_cacheable_output
from chat.domain.entities import VisionImage

_MCP_CACHE_PATHS_KEY = "__mcp_cache_paths__"
_MCP_CACHE_PAYLOAD_KEY = "payload"


class McpRemoteTool(Tool):
    def __init__(
        self,
        *,
        mcp_client: Any,
        server: Any,
        remote_name: str,
        definition: ToolDefinition,
        failure_reason: str,
    ) -> None:
        self._mcp_client = mcp_client
        self._server = server
        self._remote_name = remote_name
        self._definition = definition
        self._failure_reason = failure_reason

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        try:
            tool_context = {
                key: context[key]
                for key in self._definition.policy.required_context_keys
                if key in context
            }

            if self._server is None: # 内部 MCP
                result = await self._mcp_client.call_tool(
                    self._server,
                    self._remote_name,
                    kwargs,
                    tool_config=config,
                    tool_context=tool_context,
                    timeout_seconds=self._definition.policy.timeout_seconds,
                )
            else:
                result = await self._mcp_client.call_tool(
                    self._server,
                    self._remote_name,
                    kwargs,
                    timeout_seconds=self._definition.policy.timeout_seconds
                )
            output = _tool_output_from_result(result)
            if getattr(result, "isError", False):
                raise ToolExecutionError(
                    reason=self._failure_reason,
                    detail_reason=output.content or f"MCP tool '{self._remote_name}' returned an error.",
                    retryable=False,
                )
            output = await _process_mcp_cache_envelope(output, context=context)
            return output
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                reason=self._failure_reason,
                detail_reason=str(e),
                retryable=False,
            ) from e


async def _process_mcp_cache_envelope(
    output: ToolOutput,
    *,
    context: dict[str, Any],
) -> ToolOutput:
    """识别 MCP 业务信封，在 Chat Host 内完成缓存并只返回脱壳 payload。"""

    try:
        envelope = json.loads(output.content)
    except (TypeError, json.JSONDecodeError):
        return output

    if not _is_mcp_cache_envelope(envelope):
        return output

    paths = tuple(envelope[_MCP_CACHE_PATHS_KEY])
    payload = envelope[_MCP_CACHE_PAYLOAD_KEY]
    session_id = context.get("session_id")
    if isinstance(session_id, str) and session_id:
        try:
            payload = await process_cacheable_output(
                payload,
                paths=paths,
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001 - cache enhancement must not fail the MCP result
            # 缓存属于输出增强；Store 故障不能让远程 MCP 主结果失败。
            payload = envelope[_MCP_CACHE_PAYLOAD_KEY]

    return ToolOutput(
        content=json.dumps(payload, ensure_ascii=False),
        images=output.images,
    )


def _is_mcp_cache_envelope(value: Any) -> bool:
    """只接受当前 MCP 信封的两个字段，避免误吞普通业务对象。"""

    if not isinstance(value, dict):
        return False
    if set(value) != {_MCP_CACHE_PATHS_KEY, _MCP_CACHE_PAYLOAD_KEY}:
        return False
    paths = value[_MCP_CACHE_PATHS_KEY]
    return isinstance(paths, list) and all(
        isinstance(path, str) and bool(path.strip()) for path in paths
    )


def _tool_output_from_result(result: CallToolResult) -> ToolOutput:
    # MCP 的 CallToolResult 可以同时携带 structuredContent 和 content blocks。
    # content blocks 里可能有图片；即使最终正文优先采用 structuredContent，也不能跳过图片提取。
    parts: list[dict[str, Any]] = []
    images: list[VisionImage] = []
    for item in result.content:
        # MCP SDK 的 block 通常是 Pydantic model；先转成协议字段名的 JSON dict，避免直接访问 item.text 误伤 image/resource/audio。
        if hasattr(item, "model_dump"):
            block = item.model_dump(mode="json", by_alias=True)
        else:
            # 非标准对象兜底为文本块，保留可读信息，不让奇怪对象直接破坏工具调用链路。
            text = getattr(item, "text", None)
            block = {"type": "text", "text": str(text) if text is not None else str(item)}

        block_type = block.get("type")
        if block_type == "text":
            parts.append(block)
            continue
        if block_type == "image":
            # MCP ImageContent: data 是 base64，mimeType 是图片 MIME；映射到运行时 VisionImage，不写入持久化消息正文。
            data = block.get("data")
            mime_type = block.get("mimeType")
            if data is not None and mime_type is not None:
                images.append(
                    VisionImage(
                        media_type=str(mime_type),
                        base64_data=str(data),
                    )
                )
            continue
        parts.append(block)

    if result.structuredContent is not None:
        # structuredContent 是 MCP 的机器可读主输出；存在时正文优先使用它，图片仍从 content blocks 透传给模型。
        return ToolOutput(
            content=json.dumps(
                normalize_json_value(result.structuredContent),
                ensure_ascii=False,
            ),
            images=images,
        )

    if parts:
        text_parts = [
            str(item["text"])
            for item in parts
            if item.get("type") == "text" and item.get("text") is not None
        ]
        if text_parts:
            # 普通文本工具的最友好路径：多个 text block 按顺序拼成纯文本。
            return ToolOutput(content="\n".join(text_parts), images=images)
        # 没有 text block 时，可能是 resource/audio 等非文本内容；保留完整 block JSON，避免协议细节丢失。
        return ToolOutput(content=json.dumps(normalize_json_value(parts), ensure_ascii=False), images=images)

    # 允许纯图片工具：正文为空，图片进入 runtime images。
    return ToolOutput(content="", images=images)
