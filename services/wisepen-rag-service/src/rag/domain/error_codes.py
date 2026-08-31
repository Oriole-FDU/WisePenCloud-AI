"""RAG v2 对外服务错误码。"""

from common.core.domain import IErrorCode


class RagErrorCode(IErrorCode):
    NAVIGATION_INVALID = (42001, "知识导航参数不合法")
    NAVIGATION_STATE_NOT_FOUND = (42002, "知识导航状态不存在")
    NAVIGATION_STATE_INVALIDATED = (42003, "知识导航状态已失效")
    RESOURCE_CONTENT_NOT_FOUND = (42004, "资源内容不存在或不可访问")
    NAVIGATION_FAILED = (52001, "知识导航服务不可用")
    RESOURCE_READ_FAILED = (52002, "资源读取服务不可用")
