"""整轮聊天的 Langfuse 埋点；不加载业务配置、不启动服务、不改变业务异常。"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Link, Status, StatusCode


_logger = logging.getLogger("wisepen.langfuse")
_client: Any | None = None
_propagate_attributes: Any | None = None
_mask: SensitiveMask | None = None
_max_text_chars = 16000
_current_turn: ContextVar[TurnObservation | None] = ContextVar("langfuse_turn", default=None)
_reported_failures: set[str] = set()


def _report_failure(operation: str, exc: Exception) -> None:
    # 第三方异常可能携带配置内容，只输出类型；避免每个 token 重复输出警告。
    if operation not in _reported_failures:
        _reported_failures.add(operation)
        _logger.warning("langfuse %s failed; telemetry skipped. error_type=%s", operation, type(exc).__name__)


class SensitiveMask:
    def __init__(self, values=()):
        self._values = {str(value) for value in values if value}
        self._lock = Lock()

    def remember(self, value: str | None) -> None:
        if value:
            with self._lock:
                self._values.add(value)

    def text(self, value: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for secret in values:
            value = value.replace(secret, "[redacted]")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted-api-key]", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [redacted]", value)

    def __call__(self, *, data, **_):
        if isinstance(data, str):
            return self.text(data)
        if isinstance(data, dict):
            return {
                key: "[redacted]" if re.search(r"(?i)(api.?key|secret|password|authorization|cookie)", str(key))
                else self(data=value)
                for key, value in data.items()
            }
        if isinstance(data, (list, tuple)):
            return [self(data=value) for value in data]
        return data


class SanitizingSpanExporter(SpanExporter):
    """生成 Langfuse 专用副本：脱敏异常事件，并保证整轮是该后端的根节点。"""

    def __init__(self, exporter: SpanExporter, mask: SensitiveMask):
        self._exporter = exporter
        self._mask = mask

    def export(self, spans):
        safe_spans = []
        for span in spans:
            # Langfuse 通道导出整轮及模型子节点，上游 HTTP 父节点被过滤。根 Span 不能保留
            # 指向缺失节点的 parent，否则 4.59.0 的 Session 根节点回退无法读取问答。
            # 仅调整导出副本，原始 Span、Trace/Span ID 和其他 exporter 的父子关系保持原样。
            parent = None if span.name == "agent.turn" else span.parent
            safe_spans.append(ReadableSpan(
                name=span.name,
                context=span.context,
                parent=parent,
                resource=span.resource,
                attributes=self._mask(data=dict(span.attributes or {})),
                events=[Event(
                    name=self._mask.text(event.name),
                    attributes=self._mask(data=dict(event.attributes or {})),
                    timestamp=event.timestamp,
                ) for event in span.events],
                links=[Link(link.context, attributes=self._mask(data=dict(link.attributes or {}))) for link in span.links],
                kind=span.kind,
                status=Status(span.status.status_code, self._mask.text(span.status.description) if span.status.description else None),
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            ))
        return self._exporter.export(safe_spans)

    def shutdown(self):
        self._exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._exporter.force_flush(timeout_millis)


def initialize_langfuse(*, enabled: bool, base_url: str = "", public_key: str = "",
                        secret_key: str = "", environment: str = "default",
                        max_text_chars: int = 16000, sensitive_values=()) -> bool:
    global _client, _propagate_attributes, _mask, _max_text_chars
    if not enabled:
        return False
    if _client is not None:
        return True
    try:
        public_key = public_key.strip()
        secret_key = secret_key.strip()
        base_url = base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if (not public_key or not secret_key or parsed.scheme not in {"http", "https"}
                or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Missing or invalid Langfuse client configuration")
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            raise RuntimeError("Initialize the existing OpenTelemetry provider before Langfuse")
        from langfuse import Langfuse, propagate_attributes
        from langfuse.span_filter import is_langfuse_span
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        mask = SensitiveMask((*sensitive_values, public_key, secret_key))
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        mask.remember(auth)
        exporter = SanitizingSpanExporter(OTLPSpanExporter(
            endpoint=base_url + "/api/public/otel/v1/traces",
            headers={"Authorization": "Basic " + auth},
            timeout=5,
        ), mask)
        _client = Langfuse(
            public_key=public_key, secret_key=secret_key, base_url=base_url,
            environment=environment, tracer_provider=provider,
            span_exporter=exporter, mask=mask, timeout=5, flush_interval=1,
            should_export_span=lambda span: is_langfuse_span(span) and (
                span.name == "agent.turn" or (
                    span.name == "llm.generation"
                    and (span.attributes or {}).get("langfuse.observation.type") == "generation"
                ) or (
                    (span.attributes or {}).get("langfuse.observation.type") == "tool"
                    and (span.attributes or {}).get("wisepen.observation.kind") == "server_tool"
                )
            ),
        )
        _propagate_attributes = propagate_attributes
        _mask = mask
        _max_text_chars = max(256, min(int(max_text_chars), 100000))
        _logger.info("langfuse observability enabled. environment=%s", environment)
        return True
    except Exception as exc:
        _report_failure("initialize", exc)
        return False


def shutdown_langfuse() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        try:
            # SDK shutdown 会 flush 队列；不会关闭共享的 TracerProvider。
            client.shutdown()
        except Exception as exc:
            _report_failure("shutdown", exc)


@dataclass
class TurnObservation:
    span: Any = None
    metadata: dict = field(default_factory=dict)
    result: str = "running"
    input_text: str | None = None
    output_text: str = ""
    input_chars: int = 0
    output_chars: int = 0
    error_text: str | None = None

    def _text(self, text: str) -> str:
        return _mask.text(text) if _mask else text

    def set_input(self, query: str, *, execution: str = "new") -> None:
        if self.span is not None:
            self.input_chars = len(query)
            self.input_text = self._text(query)[:_max_text_chars]
            self.metadata["execution"] = execution

    def append_text(self, delta: str) -> None:
        if self.span is not None:
            self.output_chars += len(delta)
            # 整段脱敏可识别跨 token 分片的 Key；不为每个 token 创建 observation。
            self.output_text = (self.output_text + delta)[:_max_text_chars]

    def set_output(self, text: str) -> None:
        if self.span is not None:
            self.output_chars = len(text)
            self.output_text = self._text(text)[:_max_text_chars]

    def fail(self, error: str | BaseException) -> None:
        self.result = "error"
        self.error_text = self._text(str(error))[:2000]

    def cancel(self) -> None:
        if self.result != "error":
            self.result = "cancelled"

    def wait_for_tool(self) -> None:
        if self.result == "running":
            self.result = "waiting_for_tool"

    def complete(self) -> None:
        if self.result == "running":
            self.result = "success"

    def finish(self) -> None:
        if self.span is None:
            return
        try:
            metadata = {
                **self.metadata, "result": self.result,
                "input_truncated": self.input_chars > _max_text_chars,
                "output_truncated": self.output_chars > _max_text_chars,
            }
            if self.error_text:
                metadata["error"] = self.error_text
            # Session 回放可从根 Span 读取标准聊天消息；普通 query/answer 字典不会被识别。
            # 根节点表示当前轮问答；每次模型调用的实际上下文在 Generation 子节点中记录。
            input_messages = [{"role": "user", "content": self.input_text}] if self.input_text is not None else None
            output_text = self._text(self.output_text)[:_max_text_chars]
            output_messages = [{"role": "assistant", "content": output_text}] if output_text else []
            self.span.update(
                input=input_messages,
                output=output_messages,
                metadata=metadata,
                level="ERROR" if self.result == "error" else "WARNING" if self.result == "cancelled" else "DEFAULT",
                status_message=self.error_text or self.result,
            )
            if self.result == "error":
                trace.get_current_span().set_status(Status(StatusCode.ERROR, self.error_text or "turn failed"))
        except Exception as exc:
            _report_failure("update", exc)


@contextmanager
def observe_turn(*, user_id: str, session_id: str, turn_id: str):
    observation = TurnObservation(metadata={"turn_id": turn_id, "stage": "turn"})
    stack = ExitStack()
    if _client is not None:
        try:
            observation.span = stack.enter_context(_client.start_as_current_observation(
                name="agent.turn", as_type="span",
            ))
            stack.enter_context(_propagate_attributes(
                user_id=str(user_id), session_id=str(session_id), trace_name="agent.turn",
            ))
        except Exception as exc:
            observation.span = None
            _report_failure("start", exc)
            try:
                stack.close()
            except Exception as close_error:
                _report_failure("end", close_error)
            stack = ExitStack()
    token = _current_turn.set(observation)
    try:
        yield observation
    except asyncio.CancelledError:
        observation.cancel()
        raise
    except BaseException as exc:
        observation.fail(exc)
        raise
    finally:
        observation.finish()
        _current_turn.reset(token)
        try:
            # 不把原始业务异常交给 SDK 自动记录，错误信息已通过显式标记脱敏。
            stack.close()
        except Exception as exc:
            _report_failure("end", exc)


def record_turn_input(query: str, *, execution: str = "new") -> None:
    if observation := _current_turn.get():
        observation.set_input(query, execution=execution)


def record_turn_text(delta: str) -> None:
    if observation := _current_turn.get():
        observation.append_text(delta)


def record_turn_output(text: str) -> None:
    if observation := _current_turn.get():
        observation.set_output(text)


def record_turn_model(model_info) -> None:
    if observation := _current_turn.get():
        if observation.span is not None:
            if _mask:
                _mask.remember(model_info.api_key)
            observation.metadata.update(
                model_id=str(model_info.model_id), provider_id=str(model_info.provider_id), model=model_info.model_name,
            )


def record_turn_error(error: str | BaseException) -> None:
    if observation := _current_turn.get():
        observation.fail(error)


def record_turn_cancelled() -> None:
    if observation := _current_turn.get():
        observation.cancel()


def record_turn_waiting() -> None:
    if observation := _current_turn.get():
        observation.wait_for_tool()
