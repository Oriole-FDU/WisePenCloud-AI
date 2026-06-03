import logging
import sys
import warnings
from typing import Any

from loguru import logger

# 全局 Loguru Sink 配置（整个进程只注册一次）
logger.remove()  # 移除默认 sink
logger.add(
    sys.stdout,
    level="INFO",
    colorize=True,
    enqueue=False,
)


SUPPRESSED_THIRD_PARTY_LOGGERS = (
    "ddgs",
    "httpx",
    "httpcore",
    "mem0",
    "litellm",
    "courlan",
    "htmldate",
    "trafilatura",
)

SUPPRESSED_LOG_PATTERNS = (
    "predates v3 hybrid search",
    "Failed to fetch remote model cost map",
    "missing link attribute",
)

SUPPRESSED_WARNINGS = (
    "pkg_resources is deprecated",
)

for msg in SUPPRESSED_WARNINGS:
    warnings.filterwarnings("ignore", message=msg)


class _InterceptHandler(logging.Handler):
    """将标准 logging（uvicorn / FastAPI / third-party）接管到 Loguru"""

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
            if any(pattern in msg for pattern in SUPPRESSED_LOG_PATTERNS):
                return
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging_intercept(log_level: str = "INFO"):
    """
    在应用启动时调用一次，将 uvicorn / fastapi / root logger 的输出全部接管到 Loguru
    log_level 同时控制 Loguru sink 输出级别与 root logger 放行阈值
    """
    # 重新配置 Loguru sink 级别，使其与运行时配置一致
    logger.remove()
    logger.add(
        sys.stdout,
        level=log_level.upper(),
        colorize=True,
        enqueue=False,
    )

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(handlers=[_InterceptHandler()], level=numeric_level, force=True)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        log = logging.getLogger(name)
        log.handlers = [_InterceptHandler()]
        log.propagate = False

    for name in SUPPRESSED_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)


def fmt(**fields: Any) -> str:
    """
    将任意 k=v 字段拼接为固定格式的字段后缀
    """
    if not fields:
        return ""
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    return f" | {parts}"


def _caller_module() -> str:
    frame = sys._getframe(2)
    name = frame.f_globals.get("__name__", "?")
    return name.rsplit(".", 1)[-1] if name else "?"


def log_ok(op: str, **fields: Any) -> None:
    """
    操作成功（INFO）
    格式："{module} | {op}成功 | k=v ..."
    """
    logger.opt(depth=1).info(f"{_caller_module()} | {op}成功{fmt(**fields)}")


def log_fail(op: str, error: Any, **fields: Any) -> None:
    """
    操作失败，预期内的可恢复降级（WARNING）
    格式："{module} | {op}失败 | k=v ...: {error}"
    """
    logger.opt(depth=1).warning(f"{_caller_module()} | {op}失败{fmt(**fields)}: {error}")


def log_error(op: str, error: Any, **fields: Any) -> None:
    """
    操作异常，非预期的系统故障（ERROR）
    格式："{module} | {op}异常 | k=v ...: {error}"
    """
    logger.opt(
        depth=1,
        exception=error if isinstance(error, BaseException) else None,
    ).error(f"{_caller_module()} | {op}异常{fmt(**fields)}: {error}")


def log_event(event: str, **fields: Any) -> None:
    """
    进程或生命周期事件（INFO），不对应具体操作成败
    格式："{event} | k=v ..."
    """
    logger.opt(depth=1).info(f"{event}{fmt(**fields)}")


def log_debug(message, **fields: Any) -> None:
    """
    调试信息
    格式："{message} | k=v"
    """
    logger.opt(depth=1).debug(f"[DEBUG]{message}{fmt(**fields)}")

