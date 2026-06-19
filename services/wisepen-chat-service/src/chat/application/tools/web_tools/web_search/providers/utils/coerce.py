from __future__ import annotations


def as_str(value: object) -> str:
    """将任意值强制转换为去首尾空格的字符串，None 返回空字符串。"""
    return "" if value is None else str(value).strip()


def as_str_or_none(value: object) -> str | None:
    """同 as_str，但空字符串返回 None。"""
    result = as_str(value)
    return result or None


def as_str_tuple(value: object) -> tuple[str, ...]:
    """将标量或序列强制转换为非空字符串元组。"""
    values = (value,) if isinstance(value, str) else value
    if not isinstance(values, list | tuple):
        return ()
    return tuple(result for item in values if (result := as_str(item)))


def as_dict_tuple(value: object) -> tuple[dict[str, object], ...]:
    """从序列中过滤出 dict 元素，非序列返回空元组。"""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, dict))
