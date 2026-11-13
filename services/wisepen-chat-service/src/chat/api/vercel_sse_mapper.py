"""
QueryLoopRuntime 产出的领域事件 → Vercel AI SDK Data Stream Protocol SSE 字符串
将来若要支持 OpenAI 原生 stream / WebSocket 等其他协议，新增一个同构的 *_mapper.py 即可
"""
from chat.api.vercel_formats import (
    step_start, step_finish,
    text_start, text_delta, text_end,
    reasoning_start, reasoning_delta, reasoning_end,
    tool_input_start, tool_input_delta, tool_input_available, tool_output_available,
    tool_error, tool_denied, tool_approval_request, error,
)
from chat.application.events import (
    StreamEvent, ErrorEvent,
    StepStartEvent, StepFinishEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ReasoningStartEvent, ReasoningDeltaEvent, ReasoningEndEvent,
    ToolInputStartEvent, ToolInputDeltaEvent, ToolInputAvailableEvent, ToolOutputAvailableEvent,
    ToolErrorEvent, ToolDeniedEvent, ToolApprovalRequiredEvent,
)



def to_vercel_sse(event: StreamEvent) -> str:
    """
    将 QueryLoopRuntime 产出的单个领域事件翻译为 Vercel SSE 字符串
    未知事件类型会抛 TypeError，新增 StreamEvent 子类时必须同步更新本映射表，否则在开发期就暴露遗漏，而不是生产期静默丢帧
    """
    if isinstance(event, ErrorEvent):
        return error(error_text=event.error_text)
    if isinstance(event, StepStartEvent):
        return step_start()
    if isinstance(event, StepFinishEvent):
        return step_finish()
    if isinstance(event, TextStartEvent):
        return text_start(id=event.text_id)
    if isinstance(event, TextDeltaEvent):
        return text_delta(delta=event.delta, id=event.text_id)
    if isinstance(event, TextEndEvent):
        return text_end(id=event.text_id)
    if isinstance(event, ReasoningStartEvent):
        return reasoning_start(id=event.reasoning_id)
    if isinstance(event, ReasoningDeltaEvent):
        return reasoning_delta(delta=event.delta, id=event.reasoning_id)
    if isinstance(event, ReasoningEndEvent):
        return reasoning_end(id=event.reasoning_id)
    if isinstance(event, ToolInputStartEvent):
        return tool_input_start(tool_call_id=event.call_id, tool_name=event.tool_name)
    if isinstance(event, ToolInputAvailableEvent):
        return tool_input_available(
            tool_call_id=event.call_id, tool_name=event.tool_name, input=event.input
        )
    if isinstance(event, ToolInputDeltaEvent):
        return tool_input_delta(tool_call_id=event.call_id, input_text_delta=event.delta)
    if isinstance(event, ToolOutputAvailableEvent):
        return tool_output_available(tool_call_id=event.call_id, output=event.output)
    if isinstance(event, ToolErrorEvent):
        return tool_error(tool_call_id=event.call_id, error_text=event.error_text)
    if isinstance(event, ToolDeniedEvent):
        return tool_denied(tool_call_id=event.call_id)
    if isinstance(event, ToolApprovalRequiredEvent):
        return tool_approval_request(
            approval_id=event.call_id,
            tool_call_id=event.call_id,
            tool_name=event.tool_name,
            tool_desc=event.tool_desc,
            input=event.input,
        )
    raise TypeError(f"Unknown StreamEvent subclass: {type(event).__name__}")
