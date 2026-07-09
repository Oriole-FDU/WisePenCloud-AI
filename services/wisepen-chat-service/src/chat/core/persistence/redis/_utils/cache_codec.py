from __future__ import annotations

from functools import lru_cache
from typing import Any, TypeVar

import msgspec

T = TypeVar("T")

# Redis cache 的边界统一为：结构化对象 <-> JSON bytes。
# 缓存模型应优先使用明确字段表达 schema，并在 loads_cache 调用处传入具体
# model_type，让 msgspec 在解码阶段完成类型还原和 schema validation。
# 新增字段必须提供默认值；不要改已有字段语义。只有确实需要开放扩展的
# 附加信息才放入 metadata，并且 metadata 内容应保持 JSON 原生类型。
# msgspec 覆盖 dataclass/msgspec.Struct/TypedDict、集合、Enum、datetime、
# bytes、Union 与基础标量等常见缓存载荷类型。
_ENCODER = msgspec.json.Encoder()


def dumps_cache(value: Any) -> bytes:
    """将缓存对象编码为 Redis 可直接存储的 JSON bytes。"""
    return _ENCODER.encode(value)


def loads_cache(payload: bytes | bytearray | memoryview | str, model_type: type[T] | Any) -> T:
    """将 Redis JSON payload 解码并校验为目标类型。"""
    return _decoder(model_type).decode(_normalize_payload(payload))


def loads_cache_or_none(
    payload: bytes | bytearray | memoryview | str,
    model_type: type[T] | Any,
) -> T | None:
    """将 Redis payload 解码为目标类型；损坏或 schema 不匹配时按 cache miss 处理。"""
    try:
        return loads_cache(payload, model_type)
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError):
        return None


@lru_cache(maxsize=128)
def _decoder(model_type: Any) -> msgspec.json.Decoder:
    return msgspec.json.Decoder(model_type)


def _normalize_payload(payload: bytes | bytearray | memoryview | str) -> bytes | bytearray | memoryview:
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return payload
