from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, Dict, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from chat.application.tools.core.execution.hooks.base import ToolPreflightHook


class ToolTimeoutStrategy(StrEnum):
    CANCEL_TASK = "cancel_task"
    MARK_TIMEOUT_ONLY = "mark_timeout_only"


class ToolRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

@dataclass(frozen=True)
class ToolParametersSchema:
    raw: dict[str, Any]

    def __post_init__(self) -> None:
        self._validate_schema(self.raw)

    @property
    def properties(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("properties") or {}

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(self.raw.get("required") or ())

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise TypeError("parameters_schema must be a dict.")

        if schema.get("type") != "object":
            raise ValueError("parameters_schema.type must be 'object'.")

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("parameters_schema.properties must be a dict.")

        required = schema.get("required", [])
        if not isinstance(required, (list, tuple)):
            raise ValueError("parameters_schema.required must be a list or tuple.")

        if not all(isinstance(item, str) for item in required):
            raise ValueError("parameters_schema.required must contain only strings.")

        unknown_required = [
            item for item in required
            if item not in properties
        ]

        if unknown_required:
            raise ValueError(
                f"parameters_schema.required contains keys not defined in properties: {unknown_required}"
            )

@dataclass(frozen=True)
class ToolLLMSpec:
    name: str
    description: str
    parameters_schema: ToolParametersSchema

@dataclass(frozen=True)
class ToolPolicy:
    """工具策略"""
    expose_by_default: bool = False # 是否默认暴露给模型

    timeout_seconds: float | None = None # 超时时间
    timeout_strategy: ToolTimeoutStrategy = ToolTimeoutStrategy.CANCEL_TASK # 超时后策略

    persist_output: bool = False # 是否持久化输出 (如果不持久化则需要生成占位符)
    persisted_output_placeholder_factory: Callable[[dict, Any], str | None] = lambda tool_call_arguments, output: None # 持久化输出的占位生成器

    risk_level: ToolRiskLevel = ToolRiskLevel.LOW # 风险级别

    required_context_keys: tuple[str, ...] = () # 需要的上下文Key

    max_output_chars: int | None = None # 输出最大字符数（超过后截断）
    allow_parallel: bool = False # 允许并行


@dataclass(frozen=True)
class ToolDefinition:
    llm_spec: ToolLLMSpec
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    preflight_hooks: tuple['ToolPreflightHook', ...] = ()


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        ...

    async def execute(self, context: Dict[str, Any], **kwargs) -> Any:
        ...