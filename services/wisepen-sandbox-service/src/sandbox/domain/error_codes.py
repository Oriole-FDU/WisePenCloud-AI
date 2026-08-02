from common.core.domain import IErrorCode


class SandboxErrorCode(IErrorCode):
    POOL_EMPTY = (46001, "沙箱池暂无可用实例")
    LEASE_NOT_FOUND = (46002, "沙箱租约不存在")
    LEASE_EXPIRED = (46003, "沙箱租约已过期")
    FENCING_REJECTED = (46004, "沙箱租约校验失败")
    REQUEST_CONFLICT = (46005, "请求幂等上下文冲突")
    INVALID_STATE_TRANSITION = (46006, "沙箱状态转换非法")
    WORKSPACE_PATH_INVALID = (46007, "工作区路径不合法")
    WORKSPACE_SYNC_FAILED = (46008, "沙箱工作区同步失败")
    SANDBOX_UNAVAILABLE = (46009, "沙箱服务暂不可用")
    AIO_RESOURCE_NOT_FOUND = (46010, "AIO 资源不存在")
    WORKSPACE_CACHE_LIMIT_EXCEEDED = (46011, "沙箱工作区缓存超出限制")
    INVALID_EXECUTION_TIMEOUT = (46012, "沙箱执行超时参数不合法")
