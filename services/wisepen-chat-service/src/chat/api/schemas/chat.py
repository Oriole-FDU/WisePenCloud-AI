from typing import Optional, List, Dict, Any, Set, Self
from pydantic import BaseModel, Field, model_validator

from chat.application.tools.client_tools import ClientToolCapability
from chat.application.subagents.models import SubagentOptions


class ActiveChatTurnResponse(BaseModel):
    turn_id: Optional[str] = Field(default=None, description="当前正在运行的 turn ID；不存在时为空。")


class ClientToolResultSubmission(BaseModel):
    tool_call_id: str = Field(..., min_length=1, description="客户端工具调用 ID。")
    output: Any | None = Field(default=None, description="客户端工具成功执行结果。")
    error_text: Optional[str] = Field(default=None, min_length=1, description="客户端工具执行失败原因。")

    @model_validator(mode="after")
    def validate_output_or_error(self) -> Self:
        has_output = "output" in self.model_fields_set
        has_error = "error_text" in self.model_fields_set and self.error_text is not None
        if has_output == has_error:
            raise ValueError("output 和 error_text 必须且只能提供一个")
        return self


class ToolApprovalStatusSubmission(BaseModel):
    tool_call_id: str = Field(..., min_length=1, description="待审批工具调用 ID。")
    approved: bool = Field(..., description="是否批准执行该高危工具。")


class ChatRecoverRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="目标会话 ID；必须属于当前登录用户。")
    client_tool_results: List[ClientToolResultSubmission] = Field(
        default_factory=list,
        description="客户端工具执行结果列表；没有客户端工具结果时可为空数组。",
    )
    tool_approval_status: List[ToolApprovalStatusSubmission] = Field(
        default_factory=list,
        description="高危工具审批状态列表；没有待审批工具时可为空数组。",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        result_ids = [item.tool_call_id for item in self.client_tool_results]
        approval_ids = [item.tool_call_id for item in self.tool_approval_status]

        if len(result_ids) != len(set(result_ids)):
            raise ValueError("client_tool_results.tool_call_id 不允许重复")
        if len(approval_ids) != len(set(approval_ids)):
            raise ValueError("tool_approval_status.tool_call_id 不允许重复")

        return self


class ChatCancelRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="目标会话 ID；必须属于当前登录用户。")

class SubagentControlRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    root_execution_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)


class SubagentInputRequest(SubagentControlRequest):
    input: str = Field(min_length=1, max_length=32000)

class ChatRequest(BaseModel):
    """
    聊天请求传输对象
    """
    session_id: str = Field(..., description="目标会话 ID；必须属于当前登录用户。")
    query: str = Field(..., description="本轮用户输入；不能为空，会作为最新 user message 进入 prompt。")
    subagents: SubagentOptions | None = None
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
    tool_selection_default_enabled: Optional[bool] = Field(
        default=None,
        description="本轮用户可选工具的默认启用状态；未传时沿用 Agent 配置。",
    )
    tool_selection_overrides: Optional[Dict[str, bool]] = Field(
        default=None,
        description="本轮工具启用覆盖 Map；未传时沿用 Agent 配置，传空 Map 表示清空 Agent 覆盖；CONTEXTUAL 工具仅支持用 false 显式排除。",
    )
    user_defined_on_demand_skill_ids: Optional[Set[str]] = Field(
        default=None,
        description="本轮候选 Skill ID 集合；传入时覆盖 Agent on_demand_skill_ids，并由 Skill matcher 按 query 选择可展示 Skill metadata。",
    )
    client_tool_capabilities: List[ClientToolCapability] = Field(
        default_factory=list,
        description="当前页面支持执行的客户端工具能力；只在本轮及其恢复链路中有效。",
    )
