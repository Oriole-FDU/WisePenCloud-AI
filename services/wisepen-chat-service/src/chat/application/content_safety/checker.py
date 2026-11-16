from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from chat.api.vercel_formats import error as sse_error, text_delta, text_end, text_start
from chat.application.content_safety.client import (
    ContentSafetyVerdict,
    SafetyClassifier,
)
from chat.core.config.settings_models import ContentSafetySettings
from chat.domain.entities import TurnCancelReason, TurnCancelSignal
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces.llm import LLMEventType, LLMStreamEvent
from common.core.exceptions import ServiceException
from common.logger import info, warn


def _cancel_signal(refusal: str, verdict: ContentSafetyVerdict) -> TurnCancelSignal:
    return TurnCancelSignal(
        reason=TurnCancelReason.CONTENT_SAFETY,
        meta={"refusal": refusal, "category": verdict.category, "severity": verdict.severity, "reason": verdict.reason},
    )


@dataclass
class _OutputWatcher:
    """单次 turn 的输出审查状态（仅在单个事件循环内使用）。

    输出是流式到达的 SSE delta，既无法一次性拿到完整文本，也不该每来一个字符都打一次安全模型
    这里把 delta 累积进 buffer，等尚未判定的新增文本量达到 output_chunk_chars 后，才把这一段交给安全模型异步判定
    判定任务用 asyncio.Task 在后台运行，不阻塞流式转发
    命中结果缓存在 verdict 里，供上层在下一帧时读取
    """

    client: SafetyClassifier
    config: ContentSafetySettings

    # 已累积的输出文本，checked_len 之前的部分已经（或正在）交给安全模型
    buffer: str = ""
    checked_len: int = 0

    # 首个判定为 blocked 的结果，一旦命中就不再更新
    verdict: ContentSafetyVerdict | None = None

    # 进行中的判定任务
    tasks: list[asyncio.Task] = field(default_factory=list)

    def feed(self, delta: str) -> None:
        """追加一段输出 delta，攒够一个 chunk 就触发一次判定"""
        if not delta:
            return
        self.buffer += delta

        # output_chunk_chars 阈值太小会频繁调用安全模型，太大则违规内容可能已流出一大段才被拦截
        if len(self.buffer) - self.checked_len >= self.config.output_chunk_chars:
            self._schedule()

    def _schedule(self) -> None:
        """截取尚未判定的文本，启动一个后台判定任务"""
        text = self.buffer[self.checked_len:]
        self.checked_len = len(self.buffer)

        # 纯空白（换行、缩进等）没有判定价值，直接跳过
        if not text.strip():
            return
        self.tasks.append(asyncio.create_task(self._run(text)))

    async def _run(self, text: str) -> None:
        """对一段文本做安全判定，命中则记录第一个 blocked 结果"""
        verdict = await self.client.check(text, kind="output")
        if verdict is not None and verdict.blocked and self.verdict is None:
            self.verdict = verdict

    async def aclose(self) -> None:
        """等待所有后台判定任务结束，任务异常不影响主流程"""
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


class TurnOutputSafety:
    """同一个 turn 的主输出和所有子代理共享一次阻断决定。"""

    def __init__(self, client: SafetyClassifier, config: ContentSafetySettings) -> None:
        self._client = client
        self._threshold = config.output_chunk_chars
        self._refusal = config.refusal_output
        self.verdict: ContentSafetyVerdict | None = None

    @property
    def cancel_signal(self) -> TurnCancelSignal | None:
        return _cancel_signal(self._refusal, self.verdict) if self.verdict is not None else None

    async def review_model_output(
        self, events: AsyncGenerator[LLMStreamEvent, None], on_block: Callable[[], None],
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """先审查子代理的文本和推理，再交给消息解释器。"""
        pending: list[str] = []
        kind: LLMEventType | None = None
        length = 0

        async def flush() -> LLMStreamEvent | None:
            nonlocal length
            if not pending or kind is None or self.verdict is not None:
                return None
            text = "".join(pending)
            pending.clear()
            length = 0
            if text.strip():
                verdict = await self._client.check(text, kind="output")
                if verdict is not None and verdict.blocked:
                    on_block()
                    if self.verdict is None:
                        self.verdict = verdict
            if self.verdict is None:
                return LLMStreamEvent(type=kind, delta=text)
            return None

        try:
            async for event in events:
                if event.type == LLMEventType.USAGE:
                    yield event
                    continue
                if self.verdict is not None:
                    return
                if event.type in (LLMEventType.TEXT_DELTA, LLMEventType.REASONING_DELTA):
                    if kind != event.type:
                        if (approved := await flush()) is not None:
                            yield approved
                        if self.verdict is not None:
                            return
                        kind = event.type
                    if event.delta:
                        pending.append(event.delta)
                        length += len(event.delta)
                    if length >= self._threshold:
                        if (approved := await flush()) is not None:
                            yield approved
                        if self.verdict is not None:
                            return
                elif event.type == LLMEventType.STATE:
                    yield event
                else:
                    if (approved := await flush()) is not None:
                        yield approved
                    if self.verdict is not None:
                        return
                    yield event
            if (approved := await flush()) is not None:
                yield approved
        finally:
            await events.aclose()


class ContentSafetyChecker:
    """内容安全审查器"""

    def __init__(self, client: SafetyClassifier, redis: Any) -> None:
        self._client = client
        self._config = client.config
        self._redis = redis
        self.enabled = client.enabled

    def start_output_review(self) -> TurnOutputSafety | None:
        if self.enabled and self._config is not None:
            return TurnOutputSafety(self._client, self._config)
        return None

    async def is_banned(self, user_id: str) -> bool:
        """非阻塞地查询用户是否已被封禁（存在封禁键即视为封禁）"""
        if not self.enabled:
            return False
        try:
            return bool(await self._redis.get(self._block_key(user_id)))
        except Exception as exc:  # noqa: BLE001
            warn("content safety ban check failed.", user_id=user_id, exc=exc)
            return False

    async def ensure_not_banned(self, user_id: str) -> None:
        """封禁用户不允许发起新的 turn，在产生任何副作用之前直接拒绝
        """
        if await self.is_banned(user_id):
            info("content safety banned user refused.", user_id=user_id)
            raise ServiceException(ChatErrorCode.CONTENT_SAFETY_BLOCKED, self._config.refusal_banned)

    def _banned_frames(self) -> list[str]:
        """封禁用户的封禁帧，提示对话功能已受限"""
        text_id = f"txt_{uuid.uuid4().hex}"
        notice = self._config.refusal_banned
        return [text_start(text_id), text_delta(notice, text_id), text_end(text_id)]

    async def watch_frames(
        self,
        frames: AsyncIterator[str],
        *,
        user_id: str,
        session_id: str,
        request_cancel: Callable[[TurnCancelSignal], Awaitable[None]],
        output_review: TurnOutputSafety | None = None,
    ) -> AsyncIterator[str]:
        """监听 SSE 帧，命中即置安全取消标记、注入拒答帧与安全拦截错误帧，丢弃后续帧"""
        if not self.enabled:
            async for frame in frames:
                yield frame
            return

        # 封禁时直接返回封禁帧
        if await self.is_banned(user_id):
            info("content safety banned user refused.", user_id=user_id, session_id=session_id)
            for frame in self._banned_frames():
                yield frame
            return

        watcher = _OutputWatcher(self._client, self._config)
        current_text_id: Optional[str] = None
        text_open = False
        hit = False
        try:
            async for frame in frames:
                child_verdict = output_review.verdict if output_review is not None else None
                if not hit and (verdict := child_verdict or watcher.verdict) is not None:
                    hit = True
                    await self._record_hit(user_id, session_id, verdict)
                    await request_cancel(_cancel_signal(self._config.refusal_output, verdict))
                    for refusal_frame in self._refusal_frames(current_text_id, text_open):
                        yield refusal_frame
                    yield sse_error(
                        error_text=ChatErrorCode.CONTENT_SAFETY_BLOCKED.msg,
                        error_code=ChatErrorCode.CONTENT_SAFETY_BLOCKED.name,
                    )
                if hit:
                    # 子代理终态只含已审查的输出，也携带客户端收尾所需的用量。
                    if child_verdict is not None and _is_subagent_terminal_state(frame):
                        yield frame
                    continue

                kind, text, text_id = _parse_frame(frame)
                if kind in ("text-delta", "reasoning-delta"):
                    watcher.feed(text)
                if kind == "text-start":
                    current_text_id = text_id
                    text_open = True
                elif kind == "text-delta" and text_id is not None:
                    current_text_id = text_id
                elif kind == "text-end":
                    text_open = False
                yield frame
        finally:
            await watcher.aclose()

    def _refusal_frames(
        self, current_text_id: Optional[str], text_open: bool
    ) -> list[str]:
        """构造拒答 SSE 帧，用于替换被拦截的输出
        若命中时已有打开中的文本块（text-start 已发、text-end 未发），就在该文本块内追加拒答文案并正常收尾，保证客户端文本块配对
        否则新开一个完整的文本块
        """
        notice = self._config.refusal_output
        if text_open and current_text_id is not None:
            # 复用当前文本块：补一个换行 + 拒答文案，再结束该块
            return [text_delta("\n" + notice, current_text_id), text_end(current_text_id)]
        # 没有打开的文本块：从头构造 start / delta / end
        text_id = f"txt_{uuid.uuid4().hex}"
        return [text_start(text_id), text_delta(notice, text_id), text_end(text_id)]

    async def _record_hit(
        self, user_id: str, session_id: Optional[str], verdict: ContentSafetyVerdict
    ) -> None:
        """记录安全问题命中"""
        info(
            "content safety hit.",
            user_id=user_id,
            session_id=session_id,
            category=verdict.category,
            severity=verdict.severity,
            reason=verdict.reason,
        )
        try:
            hits = await self._redis.incr(self._hits_key(user_id))
            if hits == 1:
                await self._redis.expire(self._hits_key(user_id), self._config.window_seconds)
            if hits >= self._config.escalation_threshold:
                await self._redis.set(
                    self._block_key(user_id), "1", ex=self._config.ban_ttl_seconds, nx=True
                )
                info("content safety user banned.", user_id=user_id, hits=hits)
        except Exception as exc:  # noqa: BLE001
            warn("content safety escalation record failed.", exc=exc)

    @staticmethod
    def _hits_key(user_id: str) -> str:
        return f"wisepen:chat:content-safety:hits:{user_id}"

    @staticmethod
    def _block_key(user_id: str) -> str:
        return f"wisepen:chat:content-safety:blocked:{user_id}"


def _is_subagent_terminal_state(frame: str) -> bool:
    payload = _frame_payload(frame)
    if payload.get("type") != "data-subagent":
        return False
    data = payload["data"]
    return data["kind"] == "state" and data["run"]["status"] != "running"


def _parse_frame(frame: str) -> tuple[str, str, Optional[str]]:
    """解析 SSE 帧，返回 (kind, text, text_id)。"""
    data = _frame_payload(frame)
    t = data.get("type")
    if t == "text-delta":
        return "text-delta", data.get("delta") or "", data.get("id")
    if t == "reasoning-delta":
        return "reasoning-delta", data.get("delta") or "", None
    if t == "text-start":
        return "text-start", "", data.get("id")
    if t == "text-end":
        return "text-end", "", data.get("id")
    return "other", "", None


def _frame_payload(frame: str) -> dict:
    if not isinstance(frame, str) or not frame.startswith("data: "):
        return {}
    try:
        data = json.loads(frame[len("data: "):])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
