from common.core.domain import IErrorCode


class SandboxErrorCode(IErrorCode):

    POOL_EMPTY = (46001, "沙箱池中没有可用的 READY 容器")
    DOCKER_RUNTIME_FAILED = (46002, "docker 命令运行错误")
    SANDBOX_UNAVAILABLE = (46003, "沙箱不可用")
    SANDBOX_BUSY = (46004, "沙箱忙，请重试")
    TASK_UNAVAILABLE = (46005, "任务不可用")
    FILE_PATH_INVALID = (46006, "文件路径无效或超出当前会话工作区")
    FILE_TOO_LARGE = (46007, "文件超过沙箱传输大小上限")
    ATTACHMENT_UNAVAILABLE = (46008, "附件不存在、已删除或不属于当前会话")
    FILE_TYPE_UNSUPPORTED = (46009, "文件类型不支持上传为云盘资源")
