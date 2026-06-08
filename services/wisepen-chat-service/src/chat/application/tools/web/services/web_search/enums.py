from enum import StrEnum


class SearcherName(StrEnum):
    """搜索引擎名称枚举，标识不同的搜索渠道提供商。"""
    FOURGET = "fourget"
    SERPER = "serper"
    TAVILY = "tavily"
    SERPAPI = "serpapi"
    EXA = "exa"
    PERPLEXITY = "perplexity"
    ANYSEARCH = "anysearch"
    BRAVE = "brave"
    WIKIPEDIA = "wikipedia"
    CUSTOM_SERPER = "custom_serper"


class ProviderMode(StrEnum):
    """搜索提供商模式，标识使用平台默认源还是用户自定义源。"""
    DEFAULT = "default"
    CUSTOM = "custom"


class SearchPurpose(StrEnum):
    """搜索目的枚举，区分召回和背景对齐两种使用场景。"""
    RECALL = "recall"
    GROUNDING = "grounding"

class SearchMode(StrEnum):
    """搜索模式枚举，控制搜索深度与预算（fast -> normal -> deep）。"""
    FAST = "fast"
    NORMAL = "normal"
    DEEP = "deep"

class QueryRole(StrEnum):
    """查询变体角色枚举，标识查询在主检索和次级检索中的位置。"""
    PRIMARY = "primary"
    SECONDARY = "secondary"
    EXTRA_1 = "extra_1"
    EXTRA_2 = "extra_2"
