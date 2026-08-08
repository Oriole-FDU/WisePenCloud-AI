from common.core.domain import IErrorCode


class SandboxErrorCode(IErrorCode):

    POOL_EMPTY = (46001, "沙箱池中没有可用的 READY 容器")
    INVALID_CONSUME_REQUEST = (46005, "消费请求必须提供标识信息")
    INVALID_STATE_TRANSITION = (46006, "无效的沙箱状态转换")
    SANDBOX_UNAVAILABLE = (46009, "沙箱服务暂时不可用")
    USER_SANDBOX_CAPACITY = (46014, "用户的沙箱数量已达到容量上限")
    INVALID_WORKSPACE_REQUEST = (46101, "工作区请求必须提供标识信息")
    WORKSPACE_SNAPSHOT_REJECTED = (46102, "工作区快照包含不支持的文件")
    WORKSPACE_PATH_UNSAFE = (46103, "工作区路径位于受管根目录之外")
