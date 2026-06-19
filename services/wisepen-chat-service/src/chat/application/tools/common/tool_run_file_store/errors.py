class ToolRunFileStoreError(Exception):
    """工具运行文件存储的基础异常。"""


class InvalidToolFileRefError(ToolRunFileStoreError):
    """传入的文件引用格式非法，或不属于当前用户/会话作用域。"""


class ToolFileNotFoundError(ToolRunFileStoreError):
    """文件引用不存在、已过期，或指向的文件已经被清理。"""


class ToolFileUnreadableError(ToolRunFileStoreError):
    """引用文件存在，但无法安全读取或校验失败。"""


class ToolFileWriteError(ToolRunFileStoreError):
    """发布文件或创建暂存目录失败。"""
