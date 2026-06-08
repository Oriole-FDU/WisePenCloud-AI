import sys
import logging
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


class _InterceptHandler(logging.Handler):
    """将标准 logging（uvicorn / FastAPI / third-party）接管到 Loguru"""

    def emit(self, record: logging.LogRecord):
        try:
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


def fmt(**fields: Any) -> str:
    """
    将任意 k=v 字段拼接为固定格式的字段后缀
    """
    if not fields:
        return ""
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    return f" | {parts}"


def log_ok(op: str, **fields: Any) -> None:
    """
    操作成功（INFO）
    格式："{op}成功 | k=v ..."
    """
    logger.opt(depth=1).info(f"{op}成功{fmt(**fields)}")


def log_fail(op: str, error: Any, **fields: Any) -> None:
    """
    操作失败，预期内的可恢复降级（WARNING）
    格式："{op}失败 | k=v ...: {error}"
    """
    logger.opt(depth=1).warning(f"{op}失败{fmt(**fields)}: {error}")


def log_error(op: str, error: Any, **fields: Any) -> None:
    """
    操作异常，非预期的系统故障（ERROR）
    格式："{op}异常 | k=v ...: {error}"
    """
    logger.opt(depth=1).error(f"{op}异常{fmt(**fields)}: {error}")


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

