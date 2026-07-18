from common.core.domain import IErrorCode


class McpErrorCode(IErrorCode):
    SKILL_INFO_INVALID = (41001, "Skill 信息不合法")
    SKILL_ASSET_INVALID = (41002, "Skill 资产不合法")
    SKILL_NOT_FOUND = (41003, "Skill 不存在")
    AI_ASSET_RESPONSE_INVALID = (51001, "AIAsset 返回数据不合法")
    SKILL_ASSET_UPLOAD_FAILED = (51002, "Skill 资产上传失败")
