from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


_OTEL_READY = False
_OTEL_LOGGER: Any | None = None
_TRACER: Any | None = None
_METER: Any | None = None


def setup_observability(
    *,
    service_name: str | None = None,
    service_version: str | None = None,
    environment: str | None = None,
) -> None:
    """初始化 OpenTelemetry SDK
    OTel 依赖缺失、协议不支持或未配置 Collector 时，都降级为本地控制台日志
    """

    global _OTEL_READY, _OTEL_LOGGER, _TRACER, _METER

    if _OTEL_READY:
        return

    try:
        # OTel 包导入失败时不阻断服务启动
        from opentelemetry import metrics, propagate, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry._logs import get_logger, set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except Exception:
        return

    # HTTP Client 插桩
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass

    # Kafka 插桩
    try:
        from opentelemetry.instrumentation.aiokafka import AIOKafkaInstrumentor
        AIOKafkaInstrumentor().instrument()
    except Exception:
        pass

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf").lower()
    if protocol not in {"http/protobuf", "http"}:
        return

    # Resource 是所有 trace/metric/log 的服务身份，Grafana/Tempo/Loki 都依赖它聚合
    attributes = {
        "service.name": service_name or os.getenv("OTEL_SERVICE_NAME") or "wisepen-python-service",
    }
    if service_version:
        attributes["service.version"] = service_version
    if environment:
        attributes["deployment.environment"] = environment

    resource = Resource.create(attributes=attributes)

    # HTTPX/FastAPI/Kafka 自动插桩产生的 span 导出到 Collector
    tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=_signal_endpoint(endpoint, "traces")))
        )
    trace.set_tracer_provider(tracer_provider)

    # M接入 OTLP exporter，具体业务指标可以后续按需补 meter
    metric_readers = []
    if endpoint:
        metric_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=_signal_endpoint(endpoint, "metrics"))
            )
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    metrics.set_meter_provider(meter_provider)

    # 业务日志用 _OTEL_LOGGER.emit 直写
    logger_provider = LoggerProvider(resource=resource)
    if endpoint:
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=_signal_endpoint(endpoint, "logs")))
    )
    set_logger_provider(logger_provider)
    if endpoint:
        # 业务日志直连 OTel logs API
        _OTEL_LOGGER = get_logger("wisepen.python")
    else:
        _OTEL_LOGGER = None

    # 传播协议统一使用 W3C TraceContext/Baggage，和 Java/APISIX 对齐
    propagators = [TraceContextTextMapPropagator()]
    if W3CBaggagePropagator is not None:
        propagators.append(W3CBaggagePropagator())
    propagate.set_global_textmap(CompositePropagator(propagators))

    _TRACER = trace.get_tracer("wisepen.python")
    _METER = metrics.get_meter("wisepen.python")
    _OTEL_READY = True
    return


def emit_log(
    *,
    severity_text: str,
    body: str,
    attributes: Mapping[str, Any] | None = None,
    event_name: str | None = None,
    exc: BaseException | None = None,
) -> None:
    if _OTEL_LOGGER is None:
        return

    try:
        from opentelemetry._logs import SeverityNumber

        severity_number = {
            "DEBUG": SeverityNumber.DEBUG,
            "INFO": SeverityNumber.INFO,
            "WARNING": SeverityNumber.WARN,
            "WARN": SeverityNumber.WARN,
            "ERROR": SeverityNumber.ERROR,
            "CRITICAL": SeverityNumber.FATAL,
        }.get(severity_text.upper(), SeverityNumber.INFO)

        # 直接调用 OTel logs API
        _OTEL_LOGGER.emit(
            severity_number=severity_number,
            severity_text=severity_text.upper(),
            body=body,
            attributes=normalize_attributes(attributes or {}),
            event_name=event_name,
            exception=exc,
        )
    except Exception:
        return


def instrument_fastapi_app(app: Any) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        return


def record_exception(exc: BaseException, attributes: Mapping[str, Any] | None = None) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        # 记录到当前 span，而不是只写一条日志
        # Tempo 中能直接看到错误状态
        span = trace.get_current_span()
        span.record_exception(exc, attributes=normalize_attributes(attributes or {}))
        span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    except Exception:
        return


def _signal_endpoint(base: str, signal: str) -> str:
    # 支持传入 Collector 根地址，也支持直接传 /v1/traces 这类信号端点
    cleaned = base.rstrip("/")
    if cleaned.endswith(f"/v1/{signal}"):
        return cleaned
    return f"{cleaned}/v1/{signal}"


def normalize_attributes(attributes: Mapping[str, Any]) -> dict[str, str | bool | int | float]:
    # OTel attributes 只接受 str/bool/int/float
    # bytes 和复杂对象在边界处规整
    normalized: dict[str, str | bool | int | float] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            normalized[str(key)] = value
        elif isinstance(value, BaseException):
            normalized[str(key)] = f"{type(value).__name__}: {value}"
        elif isinstance(value, bytes):
            normalized[str(key)] = value.decode("utf-8", errors="replace")
        else:
            normalized[str(key)] = str(value)
    return normalized
