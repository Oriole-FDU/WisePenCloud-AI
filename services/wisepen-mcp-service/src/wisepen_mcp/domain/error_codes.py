from common.core.domain import IErrorCode


class McpErrorCode(IErrorCode):
    SKILL_INFO_INVALID = (41001, "Skill 信息不合法")
    SKILL_ASSET_INVALID = (41002, "Skill 资产不合法")
    SKILL_NOT_FOUND = (41003, "Skill 不存在")
    NOTE_AI_REQUEST_INVALID = (41004, "Note AI 请求不合法")
    RESOURCE_REQUEST_INVALID = (41005, "Resource 请求不合法")
    WEB_SEARCH_INVALID = (41010, "Web Search 参数不合法")
    WEB_SEARCH_CONFIG_MISSING = (41011, "Web Search 配置缺失")
    WEB_SEARCH_CREDENTIAL_INVALID = (41012, "Web Search 凭证不可用")
    AI_ASSET_RESPONSE_INVALID = (51001, "AIAsset 返回数据不合法")
    SKILL_ASSET_UPLOAD_FAILED = (51002, "Skill 资产上传失败")
    WEB_SEARCH_FAILED = (51010, "Web Search 请求失败")
    WEB_SEARCH_UNAVAILABLE = (51011, "Web Search 服务不可用")
    WEB_SEARCH_EMPTY_RESULT = (51012, "Web Search 没有结果")
