import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Iterator, AsyncIterator, Tuple, Union

from common.logger import log_fail
from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces import LLMProvider
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from chat.application.tools.tool_scope import ToolScope


# =============================================================================
# QueryLoopRuntime 领域事件
# =============================================================================

@dataclass(frozen=True)
class StreamEvent:
    """QueryLoopRuntime 产出的领域事件基类。"""
    pass


@dataclass(frozen=True)
class StepStartEvent(StreamEvent):
    """一个 agent step 开始。coordinator 借此重置 reasoning 累加缓冲。"""
    pass


@dataclass(frozen=True)
class StepFinishEvent(StreamEvent):
    """一个 agent step 结束。"""
    pass


@dataclass(frozen=True)
class TextStartEvent(StreamEvent):
    """开始一段普通文本流。"""
    text_id: str


@dataclass(frozen=True)
class TextDeltaEvent(StreamEvent):
    """普通文本增量。delta 为纯文本，供 coordinator 累加进最终回答以用于持久化。"""
    text_id: str
    delta: str


@dataclass(frozen=True)
class TextEndEvent(StreamEvent):
    """结束一段普通文本流。"""
    text_id: str


@dataclass(frozen=True)
class ReasoningStartEvent(StreamEvent):
    """开始一段推理/思考文本流。"""
    reasoning_id: str


@dataclass(frozen=True)
class ReasoningDeltaEvent(StreamEvent):
    """推理/思考增量。delta 为纯文本，供 coordinator 累加进 reasoning 内容。"""
    reasoning_id: str
    delta: str


@dataclass(frozen=True)
class ReasoningEndEvent(StreamEvent):
    """结束一段推理/思考文本流。"""
    reasoning_id: str


@dataclass(frozen=True)
class ToolInputStartEvent(StreamEvent):
    """工具调用 input 阶段开始（id + name 已识别，args 未必齐）。"""
    call_id: str
    tool_name: str


@dataclass(frozen=True)
class ToolInputAvailableEvent(StreamEvent):
    """工具调用 input 阶段完成，args 已解析完毕。"""
    call_id: str
    tool_name: str
    input: Dict[str, Any]


@dataclass(frozen=True)
class ToolOutputAvailableEvent(StreamEvent):
    """工具执行完成，output 已产出。"""
    call_id: str
    output: Any


# =============================================================================
# 内部数据结构
# =============================================================================

@dataclass
class _ToolCallAccumulator:
    """在流式 delta 中按 index 分槽累积单个 tool_call 的碎片。"""
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True)
class _ParsedToolCall:
    """对 _ToolCallAccumulator 做 JSON 解析、降级后的结果。"""
    id: str
    name: str
    args: Dict[str, Any]


@dataclass(frozen=True)
class _StepTerminal:
    """_run_single_step 的终端控制信号，固定为 async generator 的最后一项，由 QueryLoopRuntime 外层识别并分流
    - should_continue: 本轮 finish_reason == 'tool_calls' 且有 tool accumulator 时为 True
    - new_messages:    本轮要追加到会话的 assistant (+tool) 消息；可能为空列表
    """
    should_continue: bool
    new_messages: List[ChatMessage]


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
        self.accumulators: Dict[int, _ToolCallAccumulator] = {}
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
                if idx not in self.accumulators:
                    self.accumulators[idx] = _ToolCallAccumulator()
                if tool_call_delta.id: # 累加 id（如果有）
                    self.accumulators[idx].id = tool_call_delta.id 
                if tool_call_delta.function: # 累加 function（如果有）
                    if tool_call_delta.function.name: # 累加 name
                        self.accumulators[idx].name += tool_call_delta.function.name
                    if tool_call_delta.function.arguments: # 累加 arguments
                        self.accumulators[idx].arguments += tool_call_delta.function.arguments
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

    """
    ReAct 循环主入口 (QueryLoop)
    """
    async def stream_chat_with_tool_calling(
        self,
        messages: List[ChatMessage],
        tool_scope: ToolScope,
        session_id: str,
        model_name: str,
        model_id: Optional[int] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        # 进入多轮循环
        for iteration in range(settings.AGENT_MAX_ITERATIONS):
            terminal: Optional[_StepTerminal] = None
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
                # 如果拿到的是 _StepTerminal 就存到 terminal；否则直接 yield
                if isinstance(item, _StepTerminal):
                    terminal = item
                else:
                    yield item

            assert terminal is not None
            # 统一追加消息并决定是否继续下一轮
            messages.extend(terminal.new_messages)
            if not terminal.should_continue:
                return
        else:
            # 超出最大迭代次数时兜底
            async for ev in self._emit_exhausted_warning(session_id):
                yield ev

    """
    Agent Step：发起一次流式推理 → 解析 → 若需要则执行工具
    """
    async def _run_single_step(
        self,
        messages: List[ChatMessage],
        session_id: str,
        model_name: str,
        model_id: Optional[int],
        api_base: Optional[str],
        api_key: Optional[str],
        iteration: int,
        tool_scope: ToolScope,
    ) -> AsyncIterator[Union[StreamEvent, _StepTerminal]]:
        # 发 step 开始事件
        yield StepStartEvent()

        # 创建本轮推理的 delta 解释器
        text_id = f"txt_{uuid.uuid4().hex}"
        reasoning_id = f"rsn_{uuid.uuid4().hex}"
        delta_interpreter = _StepDeltaInterpreter(text_id=text_id, reasoning_id=reasoning_id)

        finish_reason: str = "stop"

        # schema 已由 ToolScope 在构造期固化，这里零决策直读
        tool_schemas = tool_scope.schemas()

        try:
            # 调用模型流式接口
            async for chunk in self.llm.stream_chat_completion(
                messages=messages,
                model_name=model_name,
                tools=tool_schemas or None,
                api_base=api_base,
                api_key=api_key,
            ):
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason

                # 把 delta 片段交给解释器，产出 StreamEvent
                for ev in delta_interpreter.consume(choice.delta):
                    yield ev
        except ServiceException:
            raise  # 已经是业务异常，直接向上传播
        except Exception as e:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED,
                custom_msg=f"流式推理失败 (iter={iteration}): {e}",
            )

        # 关闭本轮推理的 delta 解释器
        for ev in delta_interpreter.close():
            yield ev

        # 如果没有工具调用，则结束这一轮（也结束整个循环）
        if finish_reason != "tool_calls" or not delta_interpreter.accumulators:
            yield StepFinishEvent()
            yield _StepTerminal(should_continue=False, new_messages=[])
            return
        
        # 如果有工具调用，则进入工具阶段

        # 解析工具调用
        parsed_tool_calls = self._parse_tool_calls(delta_interpreter.accumulators)

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
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.name, "arguments": json.dumps(tool_call.args)},
                }
                for tool_call in parsed_tool_calls
            ],
            ephemeral=false,
        )
        new_messages: List[ChatMessage] = [assistant_msg]

        for tool_call in parsed_tool_calls:
            # 为每个 parsed tool_call 产生两阶段 input 事件（start + available）
            yield ToolInputStartEvent(call_id=tool_call.id, tool_name=tool_call.name)
            yield ToolInputAvailableEvent(
                call_id=tool_call.id, tool_name=tool_call.name, input=tool_call.args
            )

        # 并发执行，收集 output 事件与 tool 消息
        output_events, tool_messages = await self._run_tools(
            parsed_tool_calls=parsed_tool_calls,
            tool_scope=tool_scope,
            session_id=session_id,
        )
        for ev in output_events:
            yield ev
        new_messages.extend(tool_messages)

        # 结束本轮并继续下一轮模型推理（因为调用工具）
        yield StepFinishEvent()
        yield _StepTerminal(should_continue=True, new_messages=new_messages)

    @staticmethod
    def _parse_tool_calls(
        accumulators: Dict[int, _ToolCallAccumulator],
    ) -> List[_ParsedToolCall]:
        """按流式 index 顺序（模型侧给出的稳定槽位号）解析累积的 tool_call 碎片，JSON 非法时降级为空 dict"""
        parsed_tool_calls: List[_ParsedToolCall] = []
        for idx in sorted(accumulators.keys()):
            acc = accumulators[idx]
            try:
                args = json.loads(acc.arguments) if acc.arguments else {}
            except json.JSONDecodeError:
                log_fail(
                    "tool_call arguments 解析 JSON 格式非法，降级为空 dict",
                    name=acc.name,
                )
                args = {}
            parsed_tool_calls.append(_ParsedToolCall(id=acc.id, name=acc.name, args=args))
        return parsed_tool_calls


    async def _run_tools(
        self,
        parsed_tool_calls: List[_ParsedToolCall],
        tool_scope: ToolScope,
        session_id: str,
        ephemeral_flags: List[bool],
    ) -> Tuple[List[StreamEvent], List[ChatMessage]]:
        """
        并行执行所有工具。
        返回 tool_output_available 事件列表（按 parsed 顺序）和对应的 Role.TOOL 消息列表（按 parsed 顺序）。
        每条 TOOL 消息独立按对应 tool 的 is_ephemeral_output 打 ephemeral 标，
        让 Finalizer 在 per-message 粒度上决定是否 redact，避免混合轮次里 skill 正文通过"整轮保守落盘"漏进 durable 历史
        """

        # 并发执行所有工具，return_exceptions=True 保证单工具失败不中断整轮
        raw_results = await asyncio.gather(
            *[self._invoke_tool(tc.name, tool_scope, tc.args) for tc in parsed_tool_calls],
            return_exceptions=True,
        )

        # 查出每个 tool call 对应 tool 的 is_ephemeral_output
        ephemeral_flags: List[bool] = [
            tool_scope.is_ephemeral(tool_call.name) for tool_call in parsed_tool_calls
        ]

        # 遍历结果做异常降级
        events: List[StreamEvent] = []
        tool_messages: List[ChatMessage] = []
        for tool_call, result, is_ephemeral in zip(parsed_tool_calls, raw_results, ephemeral_flags):
            # 如果某个结果是 Exception，转成字符串形式的 [Tool Execution Error]: ... 并记录日志，否则原样使用。
            # 防止原生 Exception 对象序列化进 API 请求导致异常
            if isinstance(result, Exception):
                safe_result = f"[Tool Execution Error]: {type(result).__name__}: {result}"
                log_fail("工具调用", result, name=tool_call.name, session=session_id)
            else:
                safe_result = result

            # 发 ToolOutputAvailableEvent
            events.append(ToolOutputAvailableEvent(call_id=tool_call.id, output=safe_result))
            # 构造对话历史中的 Tool 消息
            tool_messages.append(
                ChatMessage(
                    session_id=session_id,
                    role=Role.TOOL,
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=safe_result,
                    ephemeral=is_ephemeral,
                )
            )
        return events, tool_messages

    async def _invoke_tool(
        self,
        name: str,
        tool_scope: ToolScope,
        args: Dict[str, Any],
    ) -> str:
        """按 tool_scope 视图查工具并执行；未在视图内（被 deny 掉或未注册）时降级文本"""
        tool = tool_scope.get(name)
        if tool is None:
            log_fail("工具调用", "工具不在本轮 scope 视图内或未注册", name=name)
            return f"[Tool Execution Error] Unknown tool: '{name}'."
        return await tool.execute(context=tool_scope.context, **args)

    async def _emit_exhausted_warning(
        self, session_id: str
    ) -> AsyncIterator[StreamEvent]:
        """Agent 循环超出最大迭代次数时的兜底文本输出"""
        warn = f"Agent 推理超出最大迭代次数{settings.AGENT_MAX_ITERATIONS}，未能生成最终答案"
        log_fail("工具调用", warn, session=session_id)
        text_id = f"txt_{uuid.uuid4().hex}"
        yield StepStartEvent()
        yield TextStartEvent(text_id=text_id)
        yield TextDeltaEvent(text_id=text_id, delta=warn)
        yield TextEndEvent(text_id=text_id)
        yield StepFinishEvent()