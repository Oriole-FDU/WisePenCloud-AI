import json
from typing import Dict, Union

def _encode_string(s: str) -> str:
    """对字符串进行 JSON 编码，以处理换行和转义字符"""
    return json.dumps(s, ensure_ascii=False)

def text_delta(delta: str, id: str):
    """文本增量: '0:"delta"'"""
    return f'0:{_encode_string(delta)}\n'

def reasoning_delta(delta: str, id: str):
    """推理增量: 'g:"delta'"""
    return f'g:{_encode_string(delta)}\n'

def error(error_text: str):
    """错误信息"""
    return f'3:{_encode_string(error_text)}\n'

def tool_call(tool_call_id: str, tool_name: str, args: Dict):
    """完整的工具调用: '9:{"toolCallId":"...","toolName":"...","args":{...}}'"""
    return f'9:{json.dumps({"toolCallId": tool_call_id, "toolName": tool_name, "args": args})}\n'

def tool_call_result(tool_call_id: str, result: Union[Dict, str]):
    """工具调用结果: 'a:{"toolCallId":"...","result":...}'"""
    return f'a:{json.dumps({"toolCallId": tool_call_id, "result": result})}\n'

def stream_abort(reason: str):
    """流中止"""
    return f'3:{_encode_string(reason)}\n'

def source(source_id: str, document_id: str, url: str, title: str):
    """数据来源: 'h:{"sourceId":"...","documentId":"...","url":"...","title":"..."}'
    将引用的文档链接、标题等直接挂载在消息体下
    """
    return f'h:{json.dumps({"sourceId": source_id, "documentId": document_id, "url": url, "title": title})}\n'

def custom_data(data: list):
    """自定义业务数据: '2:[...]'"""
    return f'2:{json.dumps(data)}\n'

def message_annotations(annotations: list):
    """消息注解: '8:[...]'
    为当前生成的消息打上元数据标签
    """
    return f'8:{json.dumps(annotations)}\n'

def step_start(message_id: str):
    """多步推理开始: 'f:{"messageId":"..."}'"""
    return f'f:{json.dumps({"messageId": message_id})}\n'

def step_finish(finish_reason: str = "stop", usage: Dict = None, is_continued: bool = False):
    """多步推理结束: 'e:{"finishReason":"...","usage":{...},"isContinued":...}' """
    if usage is None:
        usage = {"promptTokens": 0, "completionTokens": 0}
    return f'e:{json.dumps({"finishReason": finish_reason, "usage": usage, "isContinued": is_continued})}\n'

def message_finish(finish_reason: str = "stop", usage: Dict = None):
    """消息结束: 'd:{"finishReason":"...","usage":{...}}'
    整个消息回复生成完毕，携带总Token消耗等统计数据
    """
    if usage is None:
        usage = {"promptTokens": 0, "completionTokens": 0}
    return f'd:{json.dumps({"finishReason": finish_reason, "usage": usage})}\n'

def file_attachment(data: str, mime_type: str):
    """文件附件: 'k:{"data":"...","mimeType":"..."}'
    如果 AI 生成了图表、CSV等文件附件，可通过此协议下发给前端下载或展示
    """
    return f'k:{json.dumps({"data": data, "mimeType": mime_type})}\n'

