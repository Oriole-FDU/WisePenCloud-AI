import json
import uuid
from typing import Dict, List, Optional, Iterator, AsyncIterator, Union

from beanie import PydanticObjectId

from chat.application.tools import ToolScope
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.core.llm.invocation import ToolCallMessageAccumulator, tool_call_parse
from chat.application.tools.core.llm.renderer import tool_result_renderer
from common.logger import warn
from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces import LLMProvider
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from chat.application.events import (
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    StepFinishEvent,
    StepStartEvent,
    StreamEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolInputAvailableEvent,
    ToolInputStartEvent,
    ToolOutputAvailableEvent,
)

class _StepDeltaInterpreter:
    """
    单个 Agent Step 内的 Delta 解释器
    - 按到达顺序消费 LLM 的 delta 片段
    - 维护 reasoning / text 的 start-end 生命周期
    - 累加 assistant_content / assistant_reasoning，按 index 累积 tool_call 碎片
    - 向外产出 StreamEvent
    """
    def __init__(self, text_id: str, reasoning_id: str) -> None:
        self.text_id = text_id
        self.reasoning_id = reasoning_id
        self.assistant_content: str = ""
        self.assistant_reasoning: str = ""
        self.tool_call_message_accumulators: Dict[int, ToolCallMessageAccumulator] = {}
        self._text_started: bool = False
        self._reasoning_started: bool = False

    def consume(self, delta) -> Iterator[StreamEvent]:
        """
        按到达顺序消费 LLM 的 delta 片段，并产出 0..N 个 StreamEvent
        - reasoning_content 到来时，必要时开启 reasoning_start，并产出 ReasoningDeltaEvent
        - content 到来时，必要时关闭 reasoning、开启 text_start，并产出 TextDeltaEvent
        - tool_calls 仅按 index 累积碎片，不立即产出事件
        """
        # 若 delta.reasoning_content 有值
        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
            # 若 reasoning 还没开始，发 ReasoningStartEvent
            if not self._reasoning_started:
                yield ReasoningStartEvent(reasoning_id=self.reasoning_id)
                self._reasoning_started = True
            # 把 reasoning 累加到 assistant_reasoning
            self.assistant_reasoning += delta.reasoning_content
            # 发 ReasoningDeltaEvent
            yield ReasoningDeltaEvent(
                reasoning_id=self.reasoning_id,
                delta=delta.reasoning_content,
            )
        # 若 delta.content 有值
        if delta.content:
            # 若文本流还没开始
            if not self._text_started:
                # 若 reasoning 未结束，发 ReasoningEndEvent
                if self._reasoning_started:
                    yield ReasoningEndEvent(reasoning_id=self.reasoning_id)
                    self._reasoning_started = False
                # 发 TextStartEvent
                yield TextStartEvent(text_id=self.text_id)
                self._text_started = True
            # 把文本累加到 assistant_content
            self.assistant_content += delta.content
            # 发 TextDeltaEvent
            yield TextDeltaEvent(
                text_id=self.text_id,
                delta=delta.content,
            )
        # 若 delta.tool_calls 有值
        if delta.tool_calls:
            for tool_call_delta in delta.tool_calls:
                # 按 index 找到对应 accumulator
                idx = tool_call_delta.index
                if idx not in self.tool_call_message_accumulators:
                    self.tool_call_message_accumulators[idx] = ToolCallMessageAccumulator()
                if tool_call_delta.id: # 累加 id（如果有）
                    self.tool_call_message_accumulators[idx].tool_call_id = tool_call_delta.id
                if tool_call_delta.function: # 累加 function（如果有）
                    if tool_call_delta.function.name: # 累加 name
                        self.tool_call_message_accumulators[idx].tool_name += tool_call_delta.function.name
                    if tool_call_delta.function.arguments: # 累加 arguments
                        self.tool_call_message_accumulators[idx].tool_call_argument_str += tool_call_delta.function.arguments
        # tool_call 只有在一整轮模型输出结束后，才能确定是不是完整、能不能解析

    def close(self) -> Iterator[StreamEvent]:
        """在模型流结束后补齐未闭合的 reasoning/text 生命周期，该方法应在单轮 stream 结束后调用一次"""
        if self._reasoning_started:
            yield ReasoningEndEvent(reasoning_id=self.reasoning_id)
            self._reasoning_started = False
        if self._text_started:
            yield TextEndEvent(text_id=self.text_id)
            self._text_started = False


# =============================================================================
# QueryLoopRuntime
# =============================================================================

class QueryLoopRuntime:
    """
    负责与 LLM 的全部交互：支持并行 Tool Calling（asyncio.gather）和多轮推理循环（while + MAX_ITERATIONS）
    """

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm
        self._tool_dispatcher = ToolDispatcher()

    """
    ReAct 循环主入口 (QueryLoop)
    """
    async def stream_chat_with_tool_calling(
        self,
        messages: List[ChatMessage],
        tool_scope: ToolScope,
        session_id: str,
        agent_max_iterations: Optional[int],
        model_name: str,
        model_id: Optional[PydanticObjectId] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        # 进入多轮循环
        for iteration in range(agent_max_iterations or settings.AGENT_MAX_ITERATIONS):
            step_finish_event: Optional[StepFinishEvent] = None
            # 把当前 messages、模型参数 和 tool_scope 委派给 _run_single_step()
            # 然后异步消费它的产出
            async for item in self._run_single_step(
                messages=messages,
                session_id=session_id,
                model_name=model_name,
                model_id=model_id,
                api_base=api_base,
                api_key=api_key,
                iteration=iteration,
                tool_scope=tool_scope,
            ):
                # 如果拿到的是 StepFinishEvent 就存到 step_finish_event；否则直接 yield
                if isinstance(item, StepFinishEvent):
                    step_finish_event = item
                yield item

            assert step_finish_event is not None
            if step_finish_event.is_finished:
                return
            else:
                # 统一追加消息并决定是否继续下一轮
                messages.extend(step_finish_event.intermediate_messages)
        else:
            # 超出最大迭代次数时兜底
            async for event in self._emit_exhausted_warning(session_id):
                yield event

    """
    Agent Step：发起一次流式推理 → 解析 → 若需要则执行工具
    """
    async def _run_single_step(
        self,
        messages: List[ChatMessage],
        session_id: str,
        model_name: str,
        model_id: Optional[PydanticObjectId],
        api_base: Optional[str],
        api_key: Optional[str],
        iteration: int,
        tool_scope: ToolScope,
    ) -> AsyncIterator[Union[StreamEvent, StepFinishEvent]]:
        # 发 step 开始事件
        yield StepStartEvent()

        # 创建本轮推理的 delta 解释器
        text_id = f"txt_{uuid.uuid4().hex}"
        reasoning_id = f"rsn_{uuid.uuid4().hex}"
        delta_interpreter = _StepDeltaInterpreter(text_id=text_id, reasoning_id=reasoning_id)

        finish_reason: str = "stop"

        # schema 已由 ToolScope 在构造期固化，这里直读
        tool_schemas = tool_scope.schemas()

        usage_tokens = 0
        try:
            # 调用模型流式接口
            async for chunk in self.llm.stream_chat_completion(
                messages=messages,
                model_name=model_name,
                tools=tool_schemas or None,
                api_base=api_base,
                api_key=api_key,
            ):
                usage_tokens += chunk.usage_tokens
                choices = chunk.raw.choices
                if choices: # usage chunk 的 choices 可能是空数组
                    finish_reason = choices[0].finish_reason or finish_reason

                    # 把 delta 片段交给解释器，产出 StreamEvent
                    for event in delta_interpreter.consume(choices[0].delta):
                        yield event
        except ServiceException:
            raise  # 已经是业务异常，直接向上传播
        except Exception as e:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED,
                custom_msg=f"流式推理失败 (iter={iteration}): {e}",
            )

        # 关闭本轮推理的 delta 解释器
        for event in delta_interpreter.close():
            yield event

        if usage_tokens == 0:
            # 未能正确计费，需要兜底
            usage_tokens = await self.llm.count_message_tokens(messages=messages, model_name=model_name, tools=tool_schemas or None)
            output_text = delta_interpreter.assistant_content + delta_interpreter.assistant_reasoning
            for idx in delta_interpreter.tool_call_message_accumulators.keys():
                acc = delta_interpreter.tool_call_message_accumulators[idx]
                output_text += acc.tool_call_id + acc.tool_name + acc.tool_call_argument_str
            usage_tokens += await self.llm.count_tokens(text=output_text, model_name=model_name)

        # 如果没有工具调用，则结束这一轮（也结束整个循环）
        if finish_reason != "tool_calls" or not delta_interpreter.tool_call_message_accumulators:
            final_message = ChatMessage(
                session_id=session_id,
                role=Role.ASSISTANT,
                model_id=model_id,
                content=delta_interpreter.assistant_content or "",
                reasoning_content=delta_interpreter.assistant_reasoning or None,
            )
            yield StepFinishEvent(is_finished=True, final_assistant_message=final_message, usage_tokens=usage_tokens)
            return
        
        # 如果有工具调用，则进入工具阶段

        # 解析工具调用
        invocations = tool_call_parse(
            delta_interpreter.tool_call_message_accumulators,
            query_loop_iteration=iteration,
        )

        # 构造 assistant 的 tool_calls 消息(OpenAI 协议要求)
        # 放入 new_messages,由 QueryLoopRuntime 外层统一 extend 进 messages
        assistant_msg = ChatMessage(
            session_id=session_id,
            role=Role.ASSISTANT,
            model_id=model_id,
            content=delta_interpreter.assistant_content or None,
            reasoning_content=delta_interpreter.assistant_reasoning or None,
            tool_calls=[
                {
                    "id": invocation.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": invocation.tool_name,
                        "arguments": json.dumps(invocation.tool_call_arguments),
                    },
                }
                for invocation in invocations
            ],
        )
        new_messages: List[ChatMessage] = [assistant_msg]

        for invocation in invocations:
            # 为每个 parsed tool_call 产生两阶段 input 事件（start + available）
            yield ToolInputStartEvent(
                call_id=invocation.tool_call_id,
                tool_name=invocation.tool_name,
            )
            yield ToolInputAvailableEvent(
                call_id=invocation.tool_call_id,
                tool_name=invocation.tool_name,
                input=invocation.tool_call_arguments,
            )

        # 通过工具 core 并发执行并归约结果
        tool_outputs = await self._tool_dispatcher.dispatch(invocations, tool_scope)

        for result in tool_outputs.results:
            tool = tool_scope.get(result.tool_invocation.tool_name)
            result = tool_result_renderer(result, tool.definition if tool else None)

            yield ToolOutputAvailableEvent(
                call_id=result.tool_call_id,
                output=result.tool_output,
            )
            new_messages.append(
                ChatMessage(
                    session_id=session_id,
                    role=Role.TOOL,
                    tool_call_id=result.tool_call_id,
                    name=result.tool_name,
                    content=result.tool_output,
                    persisted_output_placeholder=result.persisted_output_placeholder,
                )
            )

        # 结束本轮并继续下一轮模型推理（因为调用工具）
        yield StepFinishEvent(is_finished=False, intermediate_messages=new_messages, usage_tokens=usage_tokens)

    async def _emit_exhausted_warning(
        self, session_id: str
    ) -> AsyncIterator[StreamEvent]:
        """Agent 循环超出最大迭代次数时的兜底文本输出"""
        warning_text = f"Agent 推理超出最大迭代次数{settings.AGENT_MAX_ITERATIONS}，未能生成最终答案"
        warn("tool calling loop exhausted.", session_id=session_id)
        text_id = f"txt_{uuid.uuid4().hex}"
        yield StepStartEvent()
        yield TextStartEvent(text_id=text_id)
        yield TextDeltaEvent(text_id=text_id, delta=warning_text)
        yield TextEndEvent(text_id=text_id)
        final_message = ChatMessage(
            session_id=session_id,
            role=Role.ASSISTANT,
            content=warning_text,
        )
        yield StepFinishEvent(is_finished=True, final_assistant_message=final_message, usage_tokens=0)

