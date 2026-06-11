from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any

from loguru import logger as _loguru

from common.observability import emit_log, record_exception


class _InterceptHandler(logging.Handler):
    """把第三方库的 stdlib logging 日志桥接到 Loguru 控制台。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        _loguru.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging_intercept(log_level: str = "INFO"):
    normalized_level = (log_level or "INFO").upper()

    _loguru.remove()
    # Loguru 负责本地控制台可读输出
    _loguru.add(sys.stdout, level=normalized_level, colorize=True, enqueue=False)

    numeric_level = getattr(logging, normalized_level, logging.INFO)
    logging.basicConfig(handlers=[_InterceptHandler()], level=numeric_level, force=True)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        log = logging.getLogger(name)
        log.handlers = [_InterceptHandler()]
        log.propagate = False


def debug(event: str, **fields: Any) -> None:
    _emit("DEBUG", event, None, fields)


def info(event: str, **fields: Any) -> None:
    _emit("INFO", event, None, fields)


def warn(event: str, **fields: Any) -> None:
    _emit("WARNING", event, None, fields)


def warning(event: str, **fields: Any) -> None:
    warn(event, **fields)


def error(event: str, exc: BaseException | None = None, **fields: Any) -> None:
    _emit("ERROR", event, exc, fields)


def _emit(
    level: str,
    event: str,
    exc: BaseException | None,
    fields: Mapping[str, Any],
) -> None:
    # 事件名保持英文短句风格，并补齐句末标点，便于 Grafana/Loki 中检索。
    event_name = " ".join(str(event).strip().split()) or "event"
    if event_name[-1] not in ".!?": event_name = f"{event_name}."

    # 控制台日志把结构化字段追加成 key=value 形式
    message = event_name
    if fields:
        parts: list[str] = []
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, BaseException):
                text = f"{type(value).__name__}: {value}"
            elif isinstance(value, bytes):
                text = value.decode("utf-8", errors="replace")
            else:
                text = str(value)
            if not text:
                formatted = '""'
            elif any(ch.isspace() for ch in text) or '"' in text:
                escaped = text.replace("\\", "\\\\").replace('"', '\\"')
                formatted = f'"{escaped}"'
            else:
                formatted = text
            parts.append(f"{key}={formatted}")
        if parts:
            message = f"{event_name} {' '.join(parts)}"

    if exc is not None:
        # error(..., exc=e) 同时把异常记录到当前 span，和日志输出相互独立。
        record_exception(exc, fields)

    log = _loguru.bind(logger="wisepen")
    log.opt(depth=2, exception=exc).log(level, message)

    # 业务日志直接写 OTel logs API
    emit_log(
        severity_text=level,
        body=event_name,
        attributes={"event.name": event_name, **fields},
        event_name=event_name,
        exc=exc,
    )
