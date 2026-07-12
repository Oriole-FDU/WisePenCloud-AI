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
class ToolConfigSpec:
    schema: dict[str, Any] # 前端表单 schema
    required_keys: tuple[str, ...] = () # 哪些配置项必须有值这个 Tool 才可用
    secret_keys: tuple[str, ...] = () # 哪些字段是密钥
    version: int = 1 # 配置 schema 版本，未来 Tool 配置结构升级时用

    def __post_init__(self) -> None:
        if not isinstance(self.schema, dict): # schema 必须是 dict
            raise TypeError("config_spec.schema must be a dict.")
        if self.schema.get("type") != "object": # schema type 必须是 object
            raise ValueError("config_spec.schema.type must be 'object'.")

        properties = self.schema.get("properties", {})
        if not isinstance(properties, dict): # schema properties type 必须是 object
            raise ValueError("config_spec.schema.properties must be a dict.")

        # required_keys / secret_keys 改为 tuple 且由字符串组成
        required_keys = tuple(self.required_keys or ())
        secret_keys = tuple(self.secret_keys or ())
        if not all(isinstance(item, str) for item in required_keys):
            raise ValueError("config_spec.required_keys must contain only strings.")
        if not all(isinstance(item, str) for item in secret_keys):
            raise ValueError("config_spec.secret_keys must contain only strings.")

        # required_keys 和 secret_keys 中的 key 必须存在于 schema properties
        unknown_required = [item for item in required_keys if item not in properties]
        unknown_secret = [item for item in secret_keys if item not in properties]
        if unknown_required:
            raise ValueError(f"config_spec.required_keys contains unknown keys: {unknown_required}")
        if unknown_secret:
            raise ValueError(f"config_spec.secret_keys contains unknown keys: {unknown_secret}")

        if self.version < 1: # version 必须大于等于 1
            raise ValueError("config_spec.version must be positive.")

        object.__setattr__(self, "required_keys", required_keys)
        object.__setattr__(self, "secret_keys", secret_keys)

@dataclass(frozen=True)
class ToolPolicy:
    """工具策略"""
    expose_by_default: bool = False # 是否默认暴露给模型

    timeout_seconds: float | None = None # 超时时间
    timeout_strategy: ToolTimeoutStrategy = ToolTimeoutStrategy.CANCEL_TASK # 超时后策略

    persist_output: bool = False # 是否持久化输出 (如果不持久化则需要生成占位符)
    persisted_output_placeholder_factory: Callable[[dict, Any], str | None] = lambda tool_call_arguments, output: None # 持久化输出的占位生成器

    risk_level: ToolRiskLevel = ToolRiskLevel.LOW # 风险级别

    required_context_keys: tuple[str, ...] = () # 需要的上下文 Key
    required_allowed_builtin_skill_ids: tuple[str, ...] = () # 需要的内置 Skill

    max_output_chars: int | None = None # 输出最大字符数（超过后截断）
    allow_parallel: bool = False # 允许并行


@dataclass(frozen=True)
class ToolDefinition:
    llm_spec: ToolLLMSpec
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    config_spec: ToolConfigSpec | None = None
    preflight_hooks: tuple['ToolPreflightHook', ...] = ()


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        ...

    async def execute(
        self,
        context: Dict[str, Any],
        config: Dict[str, Any] | None = None,
        **kwargs,
    ) -> Any:
        ...
