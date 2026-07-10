from typing import Optional, List, Dict, Any, Set
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    聊天请求传输对象
    """
    session_id: str = Field(..., description="目标会话 ID；必须属于当前登录用户。")
    query: str = Field(..., description="本轮用户输入；不能为空，会作为最新 user message 进入 prompt。")
    model: Optional[str] = Field(
        default=None,
        description="可选模型 ID；未传时使用服务默认模型；若会话 Agent 禁止请求覆盖，则以 Agent 默认模型为准。",
    )
    provider_id: Optional[str] = Field(
        default=None,
        description="可选 Provider ID；用于指定当前模型的一条 active 映射，未传时选择首选映射。",
    )
    runtime_options: Dict[str, Any] = Field(
        default_factory=dict,
        description="模型运行时选项；会与目标 Provider manifest 默认值合并，并按 manifest JSON Schema 校验。",
    )
    frontend_states: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="前端状态列表；仅 disabled=false 且 value 非空的条目会注入应用上下文。",
    )
    user_defined_attachment_ids: Optional[List[str]] = Field(
        default=None,
        description="本轮用户点选的附件 ID 列表；用于在 prompt 中标记重点附件，附件主体仍来自会话已关联附件。",
    )
    user_defined_allow_tool_names: Optional[Set[str]] = Field(
        default=None,
        description="本轮工具白名单；传入时覆盖 Agent allow_tool_names，且仍受工具是否暴露和模型是否支持工具约束。",
    )
    user_defined_deny_tool_names: Optional[Set[str]] = Field(
        default=None,
        description="本轮工具黑名单；传入时覆盖 Agent deny_tool_names，用于隐藏默认暴露工具。",
    )
    user_defined_on_demand_skill_ids: Optional[Set[str]] = Field(
        default=None,
        description="本轮候选 Skill ID 集合；传入时覆盖 Agent on_demand_skill_ids，并由 Skill matcher 按 query 选择可展示 Skill metadata。",
    )
    user_defined_force_enabled_skill_ids: Optional[Set[str]] = Field(
        default=None,
        description="本轮强制展示的 Skill ID 集合；传入时覆盖 Agent force_enabled_skill_ids，命中的 Skill 不受普通候选 top-k 挤出。",
    )
