from typing import Any, List, Mapping, Optional, Set
from pydantic import BaseModel, Field, model_validator

from chat.application.agents.agent_assets import AgentAssetMeta


# 模型策略
class AgentModelPolicy(BaseModel):
    default_model_id: Optional[str] = None
    default_provider_id: Optional[str] = None
    allow_request_override: bool = True

    @classmethod
    def from_response(cls, payload: Mapping[str, Any] | None) -> "AgentModelPolicy":
        payload = payload or {}
        return cls(
            default_model_id=payload.get("defaultModelId"),
            default_provider_id=payload.get("defaultProviderId"),
            allow_request_override=_value_or_default(payload, "allowRequestOverride", True),
        )

# 工具与skill策略
class AgentToolAndSkillPolicy(BaseModel):
    # 是否允许使用工具
    enable_use_tool: bool = True
    # 用户可选工具默认是否启用
    tool_selection_default_enabled: bool = True
    # 用户可选工具启用覆盖；Contextual 工具仅允许用 False 显式排除
    tool_selection_overrides: dict[str, bool] = Field(default_factory=dict)
    # 是否允许使用Skill
    enable_use_skill: bool = True
    # 候选 Skill
    on_demand_skill_ids: Optional[Set[str]] = None
    # 匹配前Top-K
    skill_match_top_k: Optional[int] = Field(default=20, ge=0, lt=30)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any] | None) -> "AgentToolAndSkillPolicy":
        payload = payload or {}
        return cls(
            enable_use_tool=_value_or_default(payload, "enableUseTool", True),
            tool_selection_default_enabled=_value_or_default(
                payload, "toolSelectionDefaultEnabled", True
            ),
            tool_selection_overrides={
                str(name): bool(enabled)
                for name, enabled in (payload.get("toolSelectionOverrides") or {}).items()
            },
            enable_use_skill=_value_or_default(payload, "enableUseSkill", True),
            on_demand_skill_ids=_string_set(payload.get("onDemandSkillIds")),
            skill_match_top_k=_value_or_default(payload, "skillMatchTopK", 20),
        )

    @model_validator(mode="after")
    def normalize_tool_and_skill_policy(self) -> "AgentToolAndSkillPolicy":
        if not self.enable_use_tool:
            self.tool_selection_default_enabled = False
            self.tool_selection_overrides = {}
            self.enable_use_skill = False
        if not self.enable_use_skill:
            self.on_demand_skill_ids = None
        return self

# 记忆策略
class AgentMemoryPolicy(BaseModel):
    # 是否启用 Chat Memory
    enable_chat_memory: bool = True
    # 是否持久化 Chat Memory
    enable_persistence_chat_memory: bool = True

    # 是否启用 Chat Memory 总结压缩
    enable_chat_memory_summary: bool = True
    # 高水位线
    high_watermark_ratio: Optional[float] = Field(default=None, gt=0.0, le=1.0)
    # 低水位线
    low_watermark_ratio: Optional[float] = Field(default=None, gt=0.0, le=1.0)
    # 总结提示词
    summary_prompt: Optional[str] = None

    # 是否启用长期 Memory
    enable_long_term_memory: bool = True
    long_term_memory_limit: Optional[int] = Field(default=10, ge=0)
    long_term_memory_score_threshold: Optional[float] = Field(default=0.6, ge=0.0, le=1.0)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any] | None) -> "AgentMemoryPolicy":
        payload = payload or {}
        values = {
            "enable_chat_memory": _value_or_default(payload, "enableChatMemory", True),
            "enable_persistence_chat_memory": _value_or_default(payload, "enablePersistenceChatMemory", True),
            "enable_chat_memory_summary": _value_or_default(payload, "enableChatMemorySummary", True),
            "high_watermark_ratio": payload.get("highWatermarkRatio"),
            "low_watermark_ratio": payload.get("lowWatermarkRatio"),
            "summary_prompt": payload.get("summaryPrompt"),
            "enable_long_term_memory": _value_or_default(payload, "enableLongTermMemory", True),
            "long_term_memory_limit": _value_or_default(payload, "longTermMemoryLimit", 10),
            "long_term_memory_score_threshold": _value_or_default(payload, "longTermMemoryScoreThreshold", 0.6),
        }
        return cls(**values)

    @model_validator(mode="after")
    def normalize_memory_policy(self) -> "AgentMemoryPolicy":
        if not self.enable_chat_memory:
            self.enable_persistence_chat_memory = False
            self.enable_chat_memory_summary = False
        if not self.enable_chat_memory_summary:
            self.high_watermark_ratio = None
            self.low_watermark_ratio = None
            self.summary_prompt = None
        if not self.enable_long_term_memory:
            self.long_term_memory_limit = None
            self.long_term_memory_score_threshold = None
        return self

class AgentSpec(BaseModel):
    # 系统提示词
    system_prompt: str
    # TODO: AGENT.MD
    agent_md: Optional[str] = None
    # 启用标题自动生成
    auto_generate_title: bool = True
    # 计费组
    billing_group_id: Optional[str] = None
    # ReAct 最大迭代轮次
    agent_max_iterations: Optional[int] = Field(default=None, ge=1, lt=10)

    # 模型策略
    model_policy: AgentModelPolicy = Field(default_factory=AgentModelPolicy)
    # 工具与skill策略
    tool_and_skill_policy: AgentToolAndSkillPolicy = Field(default_factory=AgentToolAndSkillPolicy)
    # 记忆策略
    memory_policy: AgentMemoryPolicy = Field(default_factory=AgentMemoryPolicy)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any] | None) -> "AgentSpec":
        payload = payload or {}
        return cls(
            system_prompt=str(payload.get("systemPrompt") or ""),
            auto_generate_title=_value_or_default(payload, "autoGenerateTitle", True),
            model_policy=AgentModelPolicy.from_response(payload.get("modelPolicy")),
            tool_and_skill_policy=AgentToolAndSkillPolicy.from_response(payload.get("toolAndSkillPolicy")),
            memory_policy=AgentMemoryPolicy.from_response(payload.get("memoryPolicy")),
        )


class Agent(BaseModel):
    agent_id: str
    name: str
    description: str = ""
    source_type: str = ""
    version: int = 0
    version_status: str = ""
    # 预留：Agent 资产清单尚未进入 Chat 使用链路。
    assets_manifest: List[AgentAssetMeta] = Field(default_factory=list)
    spec: AgentSpec

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "Agent":
        latest_published_agent = payload.get("agentVersionBundle")
        return cls(
            agent_id=str(payload.get("resourceId")),
            name=str(payload.get("name") or ""),
            description=str(payload.get("description") or ""),
            source_type=str(payload.get("sourceType")),
            version=int(latest_published_agent.get("version") or 0),
            version_status=str(latest_published_agent.get("status")),
            assets_manifest=[
                AgentAssetMeta.from_response(item)
                for item in (latest_published_agent.get("assets") or [])
            ],
            spec=AgentSpec.from_response(latest_published_agent.get("spec")),
        )


def _string_set(value: Any) -> Set[str] | None:
    if value is None:
        return None
    return {str(item) for item in value if item is not None}


def _value_or_default(payload: Mapping[str, Any], key: str, default: Any) -> Any:
    value = payload.get(key)
    return default if value is None else value
