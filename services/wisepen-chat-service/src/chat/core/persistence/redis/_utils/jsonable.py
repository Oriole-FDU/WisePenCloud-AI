from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    """将 Redis 持久化载荷递归转换为 JSON 可序列化值。"""
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Enum):
        return value.value

    try:
        json.dumps(value)
    except TypeError:
        return str(value)

    return value
