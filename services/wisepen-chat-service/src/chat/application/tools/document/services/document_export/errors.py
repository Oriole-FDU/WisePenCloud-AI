class DocumentExportError(Exception):
    """文档导出异常基类。"""
    pass


class EmptyExportContentError(DocumentExportError):
    """导出内容为空异常。"""
    pass


class ExportRenderError(DocumentExportError):
    """导出渲染过程异常。"""
    pass


class ExportTimeoutError(DocumentExportError):
    """导出渲染超时异常。"""
    pass


class ExportOutputError(DocumentExportError):
    """导出输出路径或文件异常。"""
    pass