from enum import StrEnum


class FetcherName(StrEnum):
    """网页抓取器名称枚举，标识不同的抓取策略。"""
    STATIC = "static"
    STEEL = "steel"
    LOCAL_JS = "local_js"
    CACHE = "cache"


class FetchFailureReason(StrEnum):
    """抓取失败原因枚举，标识失败的具体原因。"""
    EXCEPTION = "exception"
    EMPTY_CONTENT = "empty_content"
    SHORT_CONTENT = "short_content"