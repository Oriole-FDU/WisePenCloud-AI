from common.core.domain import IErrorCode


class ChatErrorCode(IErrorCode):
    # --- 会话相关 ---
    SESSION_NOT_FOUND = (40001, "目标会话不存在")
    CONTEXT_LIMIT_EXCEEDED = (40002, "对话上下文超出模型限制")

    # --- 模型相关 ---
    LLM_GENERATION_FAILED = (50011, "大模型生成失败")

    # --- 记忆相关 ---
    MEMORY_NOT_FOUND = (40001, "目标记忆不存在")
    MEMORY_OPERATION_FAILED = (50021, "记忆操作失败")
