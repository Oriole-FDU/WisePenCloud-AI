from common.core.domain import IErrorCode


class ChatErrorCode(IErrorCode):
    # --- 会话相关 ---
    SESSION_NOT_FOUND = (40001, "目标会话不存在")
    CONTEXT_LIMIT_EXCEEDED = (40002, "对话上下文超出模型限制")
    AGENT_NOT_FOUND = (40003, "Agent 不存在或未发布")
    SESSION_AGENT_CHANGE_FORBIDDEN = (40004, "已有消息的会话不能切换 Agent")
    CHAT_REQUEST_INVALID = (40005, "completions 请求参数不合法")
    CHAT_TURN_IN_PROGRESS = (40006, "当前会话已有正在运行的对话")
    CHAT_ACTIVE_TURN_NOT_FOUND = (40007, "当前会话没有正在运行的对话")
    CHAT_WALLET_BLOCKED = (40008, "当前余额或额度不足，无法使用付费模型")

    # --- Provider 相关 ---
    PROVIDER_NOT_FOUND = (40011, "供应商不存在")
    PROVIDER_ALREADY_EXISTS = (40012, "供应商已存在")
    PROVIDER_IN_USE = (40013, "供应商仍被模型使用")
    PROVIDER_FORBIDDEN = (40014, "无权访问该供应商")

    # --- 模型相关 ---
    MODEL_NOT_FOUND = (40021, "模型不存在")
    MODEL_ALREADY_EXISTS = (40022, "模型已存在")
    MODEL_MAPPING_NOT_FOUND = (40023, "模型供应商映射不存在")
    MODEL_MAPPING_ALREADY_EXISTS = (40024, "模型供应商映射已存在")
    MODEL_SCOPE_MISMATCH = (40025, "模型、供应商或映射作用域不一致")
    MODEL_PROVIDER_TYPE_UNSUPPORTED = (40026, "供应商类型不支持该模型")
    MODEL_RUNTIME_OPTIONS_INVALID = (40027, "模型运行时参数不合法")
    MODEL_VISION_UNSUPPORTED = (40028, "当前模型不支持图片输入")
    IMAGE_INPUT_INVALID = (40029, "图片输入不合法")

    # --- Tool 相关 ---
    TOOL_NOT_FOUND = (40031, "工具不存在")
    TOOL_CONFIG_INVALID = (40032, "工具配置不合法")
    MCP_TOOL_CONFIG_NOT_FOUND = (40033, "MCP 工具配置不存在")
    MCP_TOOL_SERVER_URL_INVALID = (40034, "MCP 工具服务器不合法")
    MCP_TOOL_SERVER_UNREACHABLE = (40035, "MCP 工具服务器不可用")
    SUSPENDED_CHAT_NOT_FOUND = (40036, "SuspendedChat 不存在")
    SUSPENDED_CHAT_STATE_INVALID = (40037, "SuspendedChat 状态不合法")

    # --- 语音相关 ---
    SPEECH_PROVIDER_NOT_CONFIGURED = (40041, "语音识别 Provider 未配置")
    SPEECH_PROVIDER_UNSUPPORTED = (40042, "语音识别 Provider 不支持")

    # --- 模型相关 ---
    LLM_GENERATION_FAILED = (50011, "大模型生成失败")
    CHAT_TURN_LOCK_FAILED = (50012, "Session 处理锁获取失败")
    CHAT_MESSAGE_PERSIST_FAILED = (50013, "聊天消息持久化失败")

    # --- 记忆相关 ---
    MEMORY_NOT_FOUND = (40001, "目标记忆不存在")
    MEMORY_OPERATION_FAILED = (50021, "记忆操作失败")
