from common.core.domain import IErrorCode


class SandboxErrorCode(IErrorCode):

    POOL_EMPTY = (46001, "沙箱池中没有可用的 READY 容器")
    DOCKER_RUNTIME_FAILED = (46002, "docker 命令运行错误")
    SANDBOX_UNLOADING = (46003, "沙箱正在卸载")
    SNAPSHOT_FAILED = (46004, "沙箱快照失败")
