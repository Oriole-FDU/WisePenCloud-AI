from typing import Optional, Set
from pydantic import BaseModel, Field, model_validator


# 模型策略
class AgentModelPolicy(BaseModel):
    default_model_id: Optional[str] = None
    default_provider_id: Optional[str] = None
    allow_request_override: bool = True

# 工具与skill策略
class AgentToolAndSkillPolicy(BaseModel):
    # 是否允许使用工具
    enable_use_tool: bool = True
    # 工具白名单/黑名单
    allow_tool_names: Optional[Set[str]] = None
    deny_tool_names: Optional[Set[str]] = None
    # 是否允许使用Skill
    enable_use_skill: bool = True
    # 候选 Skill
    on_demand_skill_ids: Optional[Set[str]] = None
    # TODO:强制启用 Skill
    force_enabled_skill_ids: Optional[Set[str]] = None
    # 匹配前Top-K
    skill_match_top_k: Optional[int] = Field(default=20, ge=0, lt=30)

    @model_validator(mode="after")
    def normalize_tool_and_skill_policy(self) -> "AgentToolAndSkillPolicy":
        if not self.enable_use_tool:
            self.allow_tool_names = set()
            self.deny_tool_names = None
            self.enable_use_skill = False
        if not self.enable_use_skill:
            self.on_demand_skill_ids = None
            self.force_enabled_skill_ids = None
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


class Agent(BaseModel):
    agent_id: str
    name: str
    description: str = ""
    version: int = 0
    spec: AgentSpec