"""RAG HTTP 边界使用的业务错误码。"""

from common.core.domain import IErrorCode


class RagErrorCode(IErrorCode):
    """只表达对外可见的查询失败，不泄露资源是否真实存在。"""

    RESOURCE_NOT_VISIBLE = (42001, "资源不存在或不可访问")
    QUERY_FAILED = (52001, "RAG 查询服务不可用")
