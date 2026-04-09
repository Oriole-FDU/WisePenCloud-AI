import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

from chat.api.vercel_formats import (
    text_delta, reasoning_delta, tool_call, tool_call_result, step_start, step_finish, source
)

from common.logger import log_fail
from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces import LLMProvider
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from chat.application.tools.registry import ToolRegistry


@dataclass
class _ToolCallAccumulator:
    """在流式 delta 中按 index 分槽累积单个 tool_call 的碎片。"""
    id: str = ""
    name: str = ""
    arguments: str = ""


class LLMRunner:
    """
    负责与 LLM 的全部交互：支持并行 Tool Calling（asyncio.gather）和多轮推理循环（while + MAX_ITERATIONS）
    """

    def __init__(self, llm: LLMProvider, tool_registry: ToolRegistry) -> None:
        self.llm = llm
        self._registry = tool_registry

    async def stream_chat_with_tool_calling(
        self,
        messages: List[ChatMessage],
        session_id: str,
        user_id: str,
        model_name: str,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """
        ReAct while 循环主入口：全程 stream=True，delta.content 直接 yield 保证 TTFT；delta.tool_calls 按 index 分槽累积，流结束后并行执行所有 tool_calls；若超出 MAX_ITERATIONS 输出警告后退出
        """
        # 安全上下文，由系统注入
        tool_context: Dict[str, Any] = {"session_id": session_id, "user_id": user_id}

        for iteration in range(settings.AGENT_MAX_ITERATIONS):
            # 每次循环开始代表一个 agent_step
            yield step_start(message_id=f'msg_{uuid.uuid4().hex}')

            text_id = f"txt_{uuid.uuid4().hex}"

            accumulators: Dict[int, _ToolCallAccumulator] = {}
            finish_reason: str = "stop"
            assistant_content: str = ""

            try:
                # 发起流式推理，附带注册表中所有工具的 schema
                async for chunk in self.llm.stream_chat_completion(
                    messages=messages,
                    model_name=model_name,
                    tools=self._registry.schemas() or None,
                    api_base=api_base,
                    api_key=api_key,
                ):
                    # LiteLLMAdapter 的 stream_chat_completion yield 的是原始 chunk 对象
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason or finish_reason
                    delta = choice.delta

                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        yield reasoning_delta(delta=delta.reasoning_content, id=text_id)

                    # 普通文本内容：直接 yield，保证 TTFT
                    if delta.content:
                        assistant_content += delta.content
                        # 文本增量
                        yield text_delta(delta=delta.content, id=text_id)

                    # tool_calls delta：按 index 分槽累积碎片
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in accumulators:
                                accumulators[idx] = _ToolCallAccumulator()
                            acc = accumulators[idx]

                            if tc_delta.id:
                                acc.id = tc_delta.id

                            if tc_delta.function:
                                if tc_delta.function.name:
                                    acc.name += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    acc.arguments += tc_delta.function.arguments
            except ServiceException:
                raise  # 已经是业务异常，直接向上传播
            except Exception as e:
                raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"流式推理失败 (iter={iteration}): {e}")


            # finish_reason == "stop"：推理完成，退出循环
            if finish_reason != "tool_calls" or not accumulators:
                # 推理完成
                yield step_finish()
                break

            # finish_reason == "tool_calls"：并行执行所有工具，追加结果继续推理

            # 将 assistant 的 tool_calls 消息追加到对话历史（OpenAI 协议要求）
            parsed_tool_calls = []
            for acc in sorted(accumulators.values(), key=lambda a: a.id):
                try:
                    args = json.loads(acc.arguments) if acc.arguments else {}
                except json.JSONDecodeError:
                    log_fail("tool_call arguments 解析", "JSON 格式非法，降级为空 dict", name=acc.name)
                    args = {}
                parsed_tool_calls.append({"id": acc.id, "name": acc.name, "args": args})

            assistant_msg = ChatMessage(
                session_id=session_id,
                role=Role.ASSISTANT,
                content=assistant_content or None,
                tool_calls=[
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                    }
                    for tc in parsed_tool_calls
                ]
            )
            messages.append(assistant_msg)

            # 输出完整的 tool_call
            for tc in parsed_tool_calls:
                yield tool_call(tool_call_id=tc["id"], tool_name=tc["name"], args=tc["args"])

            # 并行执行所有工具，return_exceptions=True 确保单工具失败不中断整轮
            raw_results = await asyncio.gather(
                *[
                    self._invoke_tool(tc["name"], tool_context, tc["args"])
                    for tc in parsed_tool_calls
                ],
                return_exceptions=True,
            )

            # 类型断言：Exception → 降级文本，防止原生异常对象序列化进 API 请求导致格式崩塌
            for tc, result in zip(parsed_tool_calls, raw_results):
                if isinstance(result, Exception):
                    safe_result = f"[Tool Execution Error]: {type(result).__name__}: {result}"
                    log_fail("工具调用", result, name=tc["name"], session=session_id)
                else:
                    safe_result = result  # type: ignore[assignment]

                # 添加数据引用来源
                # yield source()

                # 输出工具调用结果
                yield tool_call_result(tool_call_id=tc["id"], result=safe_result)

                tool_msg = ChatMessage(
                    session_id=session_id,
                    role=Role.TOOL,
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    content=safe_result
                )
                messages.append(tool_msg)
        else:
            # 循环正常耗尽（未被 break），说明超出最大迭代次数
            warn = f"Agent 推理超出最大迭代次数（{settings.AGENT_MAX_ITERATIONS}），未能生成最终答案"
            log_fail("工具调用", warn, session=session_id)
            text_id = f"txt_{uuid.uuid4().hex}"
            yield text_delta(delta=warn, id=text_id)
            yield step_finish(finish_reason='iteration_exceeded')


    async def _invoke_tool(self, name: str, context: Dict[str, Any], args: Dict[str, Any]) -> str:
        """查找并执行工具，工具未注册时返回降级文本而非向上抛出。"""
        try:
            tool = self._registry.get(name)
        except KeyError:
            log_fail("工具调用", f"未注册的工具", name=name)
            return f"[Tool Error] Unknown tool: '{name}'."
        return await tool.execute(context=context, **args)
