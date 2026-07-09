import uuid
from typing import AsyncIterator, Iterator, List, Optional, Union

from jsonschema import Draft202012Validator, SchemaError, ValidationError

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
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.application.token_counter import TokenCounter
from chat.application.tools import ToolScope
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.message import MessageModelInfo, ToolCallMessage
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import LLMProvider
from chat.domain.interfaces.llm import LLMEventType, LLMStreamEvent
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.core.exceptions import ServiceException
from common.logger import warn


class _StepEventInterpreter:
    """
    单个 Agent Step 内的事件解释器
    - 按到达顺序消费 LLMProvider 传递的 LLMStreamEvent 事件
    - 维护 reasoning / text 的 start-end 生命周期
    - 收集 tool_call
    - 向外产出 StreamEvent
    """
    def __init__(self) -> None:
        self.text_id = f"txt_{uuid.uuid4().hex}"
        self.reasoning_id = f"rsn_{uuid.uuid4().hex}"

        # 内部字段 assistant content，用于积累模型消息，以供 LLMProvider 的原生载荷不适用时降级使用
        self.assistant_content: str = ""
        # 内部字段 assistant reasoning，用于积累模型思考，以供 LLMProvider 的原生载荷不适用时降级使用
        self.assistant_reasoning: str = ""
        # 工具调用列表
        self.tool_calls: list[ToolCallMessage] = []
        # LLMProvider 的原生载荷
        self.provider_payload: dict | None = None

        self._text_started: bool = False
        self._reasoning_started: bool = False

    def consume(self, item: LLMStreamEvent) -> Iterator[StreamEvent]:
        """
        按到达顺序消费 LLMProvider 传递的 LLMStreamEvent 事件，并产出 0..N 个 StreamEvent
        不处理 LLMEventType.USAGE 事件
        """
        # 处理 LLMProvider 的原生载荷
        if item.type == LLMEventType.STATE:
            self.provider_payload = item.provider_payload
            return

        # 处理 LLMProvider 的工具调用列表
        # 在一整轮模型输出结束后才能进入工具执行阶段
        if item.type == LLMEventType.TOOL_CALLS:
            self.tool_calls.extend(item.tool_calls or [])
            return

        # 若 reasoning_delta 有值
        if item.type == LLMEventType.REASONING_DELTA and item.delta:
            # 若 reasoning 还没开始，发 ReasoningStartEvent
            if not self._reasoning_started:
                yield ReasoningStartEvent(reasoning_id=self.reasoning_id)
                self._reasoning_started = True
            # 把 reasoning 累加到 assistant_reasoning
            self.assistant_reasoning += item.delta
            # 发 ReasoningDeltaEvent
            yield ReasoningDeltaEvent(reasoning_id=self.reasoning_id, delta=item.delta)
            return

        # 若 text_delta 有值
        if item.type == LLMEventType.TEXT_DELTA and item.delta:
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
            self.assistant_content += item.delta
            # 发 TextDeltaEvent
            yield TextDeltaEvent(text_id=self.text_id, delta=item.delta)

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

def _merge_runtime_options(defaults: dict, overrides: dict) -> dict:
    result = dict(defaults or {})
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_runtime_options(result[key], value)
        else:
            result[key] = value
    return result

class QueryLoopRuntime:
    """
    负责与 LLM 的全部交互：支持并行 Tool Calling（asyncio.gather）和多轮推理循环（while + MAX_ITERATIONS）
    """

    def __init__(
            self,
            llm_provider_resolver: LLMProviderResolver,
            token_counter: TokenCounter,
            tool_dispatcher: ToolDispatcher,
    ) -> None:
        self._llm_provider_resolver = llm_provider_resolver
        self._token_counter = token_counter
        self._tool_dispatcher = tool_dispatcher

    """
    ReAct 循环主入口 (QueryLoop)
    """
    async def stream_chat_with_tool_calling(
        self,
        messages: List[ChatMessage],
        tool_scope: ToolScope,
        session_id: str,
        agent_max_iterations: Optional[int],
        model_info: ModelRequestInfo,
    ) -> AsyncIterator[StreamEvent]:
        # 解析获取当前模型的 LLMProvider
        llm_provider = self._llm_provider_resolver.resolve(model_info)

        # 检查模型参数是否正确
        manifest = llm_provider.runtime_options_manifest()
        # 合并默认参数
        runtime_options = _merge_runtime_options(manifest.get("defaults") or {}, model_info.runtime_options or {})
        try:
            Draft202012Validator.check_schema(manifest["json_schema"])
            Draft202012Validator(manifest["json_schema"]).validate(runtime_options)
        except (SchemaError, ValidationError) as e:
            raise ServiceException(ChatErrorCode.MODEL_RUNTIME_OPTIONS_INVALID, custom_msg=str(e))
        model_info = model_info.with_runtime_options(runtime_options)

        # 进入多轮循环
        for iteration in range(agent_max_iterations or settings.AGENT_MAX_ITERATIONS):
            step_finish_event: Optional[StepFinishEvent] = None
            # 把当前 messages、模型参数 和 tool_scope 委派给 _run_single_step()
            # 然后异步消费它的产出
            async for item in self._run_single_step(
                messages=messages,
                session_id=session_id,
                model_info=model_info,
                llm_provider=llm_provider,
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
        model_info: ModelRequestInfo,
        llm_provider: LLMProvider,
        iteration: int,
        tool_scope: ToolScope,
    ) -> AsyncIterator[Union[StreamEvent, StepFinishEvent]]:
        # 发 step 开始事件
        yield StepStartEvent()

        # 创建本轮推理的事件解释器
        event_interpreter = _StepEventInterpreter()

        # schema 已由 ToolScope 在构造期固化；仅在模型和 LLM Provider 均声明支持工具时传给 LLM
        tool_schemas = tool_scope.schemas() \
            if model_info.support_tools and llm_provider.supports_tools() else []

        token_usage = 0
        try:
            # 调用模型流式接口，Provider 内部负责原生协议解析并产出 LLMStreamEvent 事件
            async for llm_provider_event in llm_provider.stream_chat_completion(
                messages=messages,
                model_request=model_info,
                tools=tool_schemas or None,
            ):
                if llm_provider_event.type == LLMEventType.USAGE and llm_provider_event.usage:
                    token_usage += llm_provider_event.usage.total_tokens

                # 把 LLMStreamEvent 事件交给解释器，产出 StreamEvent
                for event in event_interpreter.consume(llm_provider_event):
                    yield event
        except ServiceException:
            raise  # 已经是业务异常，直接向上传播
        except Exception as e:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED,
                custom_msg=f"流式推理失败 (iter={iteration}): {e}",
            )

        # 关闭本轮推理的事件解释器
        for event in event_interpreter.close():
            yield event

        assistant_msg = ChatMessage(
            session_id=session_id,
            role=Role.ASSISTANT,
            model_info=MessageModelInfo.from_model_request(model_info),
            content=event_interpreter.assistant_content or "",
            reasoning_content=event_interpreter.assistant_reasoning or None,
            provider_payload=event_interpreter.provider_payload, # 原生载荷
            tool_calls=event_interpreter.tool_calls
        )

        if token_usage == 0:
            # 未能正确计费，需要兜底
            token_usage += await self._token_counter.count_messages(
                messages=messages,
                model_name=model_info.model_name,
                tools=tool_schemas or None,
            ) # 统计输入 tokens
            token_usage += await self._token_counter.count_messages(
                messages=[assistant_msg],
                model_name=model_info.model_name,
            ) # 统计输出 tokens

        assistant_msg.token_usage = token_usage

        # 如果没有工具调用，则结束这一轮（也结束整个循环）
        if not event_interpreter.tool_calls:
            yield StepFinishEvent(is_finished=True, final_assistant_message=assistant_msg, token_usage=token_usage)
            return

        # 如果有工具调用，则进入工具阶段

        # 构造工具调用
        invocations = [
            ToolInvocation(
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.name,
                tool_call_arguments=tool_call.arguments,
                query_loop_iteration=iteration,
            )
            for tool_call in event_interpreter.tool_calls
        ]

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
            yield ToolOutputAvailableEvent(
                call_id=result.tool_call_id,
                output=result.tool_output,
            )
            new_messages.append(
                ChatMessage(
                    session_id=session_id,
                    role=Role.TOOL,
                    tool_call_id=result.tool_call_id,
                    tool_name=result.tool_name,
                    content=result.tool_output,
                    persisted_output_placeholder=result.persisted_output_placeholder,
                )
            )

        # 结束本轮并继续下一轮模型推理（因为调用工具）
        yield StepFinishEvent(is_finished=False, intermediate_messages=new_messages, token_usage=token_usage)

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
        yield StepFinishEvent(is_finished=True, final_assistant_message=final_message, token_usage=0)
