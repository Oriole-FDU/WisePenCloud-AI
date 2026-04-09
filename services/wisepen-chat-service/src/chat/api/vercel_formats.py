"""
AI SDK 6.x Data Stream Protocol (SSE JSON)
所有函数返回 SSE 格式字符串: "data: JSON\n\n"
"""
import json
from typing import Dict, Union


def _sse(payload: Union[dict, str]) -> str:
    """将 payload 编码为 SSE data 行"""
    data = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
    return f"data: {data}\n\n"


# =============================================================================
# 消息级别
# =============================================================================

def message_start(message_id: str) -> str:
    return _sse({"type": "start", "messageId": message_id})


def message_finish() -> str:
    return _sse({"type": "finish"})


def stream_done() -> str:
    return "data: [DONE]\n\n"


# =============================================================================
# 文本 (start / delta / end)
# =============================================================================

def text_start(id: str) -> str:
    return _sse({"type": "text-start", "id": id})


def text_delta(delta: str, id: str) -> str:
    return _sse({"type": "text-delta", "id": id, "delta": delta})


def text_end(id: str) -> str:
    return _sse({"type": "text-end", "id": id})


# =============================================================================
# 推理/深度思考 (start / delta / end)
# =============================================================================

def reasoning_start(id: str) -> str:
    return _sse({"type": "reasoning-start", "id": id})


def reasoning_delta(delta: str, id: str) -> str:
    return _sse({"type": "reasoning-delta", "id": id, "delta": delta})


def reasoning_end(id: str) -> str:
    return _sse({"type": "reasoning-end", "id": id})


# =============================================================================
# 工具调用
# =============================================================================

def tool_input_start(tool_call_id: str, tool_name: str) -> str:
    return _sse({"type": "tool-input-start", "toolCallId": tool_call_id, "toolName": tool_name})


def tool_input_available(tool_call_id: str, tool_name: str, input: Dict) -> str:
    return _sse({"type": "tool-input-available", "toolCallId": tool_call_id, "toolName": tool_name, "input": input})


def tool_output_available(tool_call_id: str, output: Union[Dict, str]) -> str:
    return _sse({"type": "tool-output-available", "toolCallId": tool_call_id, "output": output})


# =============================================================================
# 步骤
# =============================================================================

def step_start() -> str:
    return _sse({"type": "start-step"})


def step_finish() -> str:
    return _sse({"type": "finish-step"})


# =============================================================================
# 来源引用
# =============================================================================

def source_url(source_id: str, url: str) -> str:
    return _sse({"type": "source-url", "sourceId": source_id, "url": url})


# =============================================================================
# 错误 / 中止
# =============================================================================

def error(error_text: str) -> str:
    return _sse({"type": "error", "errorText": error_text})


def abort(reason: str) -> str:
    return _sse({"type": "abort", "reason": reason})
