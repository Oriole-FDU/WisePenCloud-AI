class DocumentParseError(Exception):
    """文档解析异常基类。"""
    pass


class UnsupportedDocumentFormatError(DocumentParseError):
    """不支持的文档格式异常，携带后缀名称和引导建议。"""
    def __init__(self, suffix: str, guidance: str = ""):
        """初始化异常，可选 guidance 告知调用方正确的处理路径。"""
        self.suffix = suffix
        self.guidance = guidance
        message = f"Unsupported document type: {suffix}"
        if guidance:
            message = f"{message}. {guidance}"
        super().__init__(message)


class EmptyParsedContentError(DocumentParseError):
    """文档解析后文本为空异常。"""
    def __init__(self, path: str):
        """初始化异常，记录导致空结果的文档路径。"""
        self.path = path
        super().__init__(f"No text extracted from document: {path}")
