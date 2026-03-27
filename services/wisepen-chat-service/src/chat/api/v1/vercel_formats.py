import json
from typing import Dict, Union

def message_start(message_id: str):
    """消息开始"""
    return f"data: {json.dumps({'type': 'start', 'messageId': message_id})}\n\n"

def text_start(id: str):
    """文本开始"""
    return f"data: {json.dumps({'type': 'text-start', 'id': id})}\n\n"

def text_delta(delta: str, id: str):
    """文本增量"""
    return f"data: {json.dumps({'type': 'text-delta', 'id': id, 'delta': delta})}\n\n"

def text_end(id: str):
    """文本结束"""
    return f"data: {json.dumps({'type': 'text-end', 'id': id})}\n\n"

def reasoning_start(id: str):
    """推理开始"""
    return f"data: {json.dumps({'type': 'reasoning-start', 'id': id})}\n\n"

def reasoning_delta(delta: str, id: str):
    """推理增量"""
    return f"data: {json.dumps({'type': 'reasoning-delta', 'id': id, 'delta': delta})}\n\n"

def reasoning_end(id: str):
    """推理结束"""
    return f"data: {json.dumps({'type': 'reasoning-end', 'id': id})}\n\n"

def source_url(url: str, source_id: str):
    """引用来源"""
    return f"data: {json.dumps({'type': 'source-url', 'sourceId': source_id, 'url': url})}\n\n"

def source_document(media_type: str, title: str, source_id: str):
    """文档引用"""
    return f"data: {json.dumps({'type': 'source-document', 'sourceId': source_id, 'mediaType': media_type, 'title': title})}\n\n"

def file(url: str, media_type: str):
    """文件引用"""
    return f"data: {json.dumps({'type': 'file', 'url': url, 'mediaType': media_type})}\n\n"

def custom_data(data_type: str, data: Dict):
    """自定义数据
    e.g. data: {"type":"data-weather","data":{"location":"SF","temperature":100}}
    """
    data_type = f'data-{data_type}'
    return f"data: {json.dumps({'type': data_type, 'data': data})}\n\n"

def error(error_text: str):
    """错误信息"""
    return f"data: {json.dumps({'type': 'error', 'errorText': error_text})}\n\n"

def tool_input_start(tool_name: str, tool_call_id: str):
    """开始调用工具"""
    return f"data: {json.dumps({'type': 'tool-input-start', 'toolCallId': tool_call_id, 'toolName': tool_name})}\n\n"

def tool_input_delta(input_text_delta: str, tool_call_id: str):
    """工具参数增量"""
    return f"data: {json.dumps({'type': 'tool-input-delta', 'toolCallId': tool_call_id, 'inputTextDelta': input_text_delta})}\n\n"

def tool_input_available(tool_name: str, input: Dict, tool_call_id: str):
    """完整的工具参数
    data: {"type":"tool-input-available","toolCallId":"call_fJdQDqnXeGxTmr4E3YPSR7Ar","toolName":"getWeatherInformation","input":{"city":"San Francisco"}}
    """
    return f"data: {json.dumps({'type': 'tool-input-available', 'toolCallId': tool_call_id, 'toolName': tool_name,'input': input})}\n\n"

def tool_output_available(output: Union[Dict, str], tool_call_id: str):
    """工具调用结果
    data: {"type":"tool-output-available","toolCallId":"call_fJdQDqnXeGxTmr4E3YPSR7Ar","output":{"city":"San Francisco","weather":"sunny"}}
    """
    return f"data: {json.dumps({'type': 'tool-output-available', 'toolCallId': tool_call_id, 'output': output})}\n\n"

def step_start():
    """一轮 LLM 调用开始"""
    return f"data: {json.dumps({'type': 'start-step'})}\n\n"

def step_finish():
    """一轮 LLM 调用结束"""
    return f"data: {json.dumps({'type': 'finish-step'})}\n\n"

def message_finish():
    """消息结束"""
    return f"data: {json.dumps({'type': 'finish'})}\n\n"

def stream_abort(reason: str):
    """流中止"""
    return f"data: {json.dumps({'type': 'abort', 'reason': reason})}\n\n"

def stream_end():
    """流结束"""
    return "data: [DONE]\n\n"

