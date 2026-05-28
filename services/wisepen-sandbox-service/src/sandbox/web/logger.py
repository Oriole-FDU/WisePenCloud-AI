from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
from typing import Any, Dict, Optional


class Logger(ABC):
    @abstractmethod
    def info(self, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def warning(self, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def error(self, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError


class StdLogger(Logger):
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        self._logger.info(self._format(message, fields))

    def warning(self, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        self._logger.warning(self._format(message, fields))

    def error(self, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        self._logger.error(self._format(message, fields))

    def _format(self, message: str, fields: Optional[Dict[str, Any]]) -> str:
        if not fields:
            return message
        try:
            payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            payload = str(fields)
        return f"{message} | {payload}"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def get_logger(name: str = "wisepen-sandbox-service") -> StdLogger:
    return StdLogger(logging.getLogger(name))
