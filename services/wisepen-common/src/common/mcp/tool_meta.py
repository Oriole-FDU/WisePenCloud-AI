from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolSelectionMode(StrEnum):
    USER_SELECTABLE = "user_selectable"
    CONTEXTUAL = "contextual"


class OutputCacheStrategy(StrEnum):
    NO_CACHE = "no_cache"
    ALWAYS_KEEP_RAW = "always_keep_raw"
    LONG_TEXT = "long_text"
    OPAQUE = "opaque"


class ToolPolicyMeta(BaseModel):
    """工具运行时策略"""

    expose_by_default: bool = False
    selection_mode: ToolSelectionMode = ToolSelectionMode.USER_SELECTABLE
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    timeout_seconds: float | None = None
    persist_output: bool = False
    required_context_keys: tuple[str, ...] = ()
    required_allowed_builtin_skill_ids: tuple[str, ...] = ()
    max_output_chars: int | None = None # 输出最大字符数（超过后截断）


class ToolUISpecMeta(BaseModel):
    display_name: str
    description: str | None = None


class ToolConfigSpecMeta(BaseModel):
    config_schema: dict[str, Any]
    required_keys: tuple[str, ...] = ()
    secret_keys: tuple[str, ...] = ()


class OutputCacheSpecMeta(BaseModel):
    """输出缓存策略"""

    strategy: OutputCacheStrategy = OutputCacheStrategy.NO_CACHE
    cache_paths: tuple[str, ...] = ()


class ToolMeta(BaseModel):
    """MCP 工具的 Chat 侧元数据"""

    tool_name: str
    policy: ToolPolicyMeta
    ui_spec: ToolUISpecMeta | None = None
    config_spec: ToolConfigSpecMeta | None = None
    output_cache_spec: OutputCacheSpecMeta = Field(default_factory=OutputCacheSpecMeta)
    failure_reason: str


__all__ = [
    "OutputCacheSpecMeta",
    "OutputCacheStrategy",
    "ToolConfigSpecMeta",
    "ToolMeta",
    "ToolPolicyMeta",
    "ToolRiskLevel",
    "ToolSelectionMode",
    "ToolUISpecMeta",
]
