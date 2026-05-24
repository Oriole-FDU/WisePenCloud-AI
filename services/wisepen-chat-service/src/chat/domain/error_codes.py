from common.core.domain import IErrorCode


class ChatErrorCode(IErrorCode):
    # --- 会话相关 ---
    SESSION_NOT_FOUND = (40001, "目标会话不存在")
    CONTEXT_LIMIT_EXCEEDED = (40002, "对话上下文超出模型限制")
    CHAT_EMPTY_INPUT = (40003, "缺少有效输入内容")
    ATTACHMENT_FILE_TYPE_UNSUPPORTED = (40031, "附件文件类型不支持")
    ATTACHMENT_FILE_TOO_LARGE = (40032, "附件文件大小超出限制")
    ATTACHMENT_NOT_FOUND = (40033, "目标附件不存在")
    ATTACHMENT_MODEL_NOT_FOUND = (40034, "目标模型不存在")
    ATTACHMENT_IMAGE_MODEL_UNSUPPORTED = (40035, "当前模型不支持图像附件")
    ATTACHMENT_STATUS_INVALID = (40036, "当前附件状态不允许执行该操作")
    ATTACHMENT_LIBRARY_FOLDER_NOT_FOUND = (40037, "目标文档库文件夹不存在")
    # 文件未通过安全审核（图像校验或有害关键词匹配）
    ATTACHMENT_AUDIT_REJECTED = (40038, "文件未通过安全审核")

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

    # --- 模型相关 ---
    LLM_GENERATION_FAILED = (50011, "大模型生成失败")

    # --- 记忆相关 ---
    MEMORY_NOT_FOUND = (40001, "目标记忆不存在")
    MEMORY_OPERATION_FAILED = (50021, "记忆操作失败")
