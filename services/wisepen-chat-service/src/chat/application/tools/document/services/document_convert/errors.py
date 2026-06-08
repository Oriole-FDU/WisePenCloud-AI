class DocumentConvertError(Exception):
    
    pass


class EmptyParsedMarkdownError(DocumentConvertError):
    def __init__(self):
        super().__init__("Document parsers returned empty Markdown content.")


class InvalidDocumentRefError(DocumentConvertError):
    def __init__(self):
        super().__init__("Invalid document file reference.")


class UnreadableDocumentRefError(DocumentConvertError):
    def __init__(self):
        super().__init__("Unable to read document file reference.")


class UnsupportedDocumentRouteError(DocumentConvertError):
    def __init__(self):
        super().__init__(
            "Conversion from the source format to the target format is unsupported."
        )


class DocumentDecodeError(DocumentConvertError):
    def __init__(self):
        super().__init__("Failed to decode document content.")


class DocumentParseFailedError(DocumentConvertError):
    def __init__(self, detail: str = ""):
        msg = "Failed to parse the document."
        if detail:
            msg = f"{msg} {detail}"
        super().__init__(msg)


class DocumentExportFailedError(DocumentConvertError):
    def __init__(self, detail: str = ""):
        msg = "Failed to export the converted document."
        if detail:
            msg = f"{msg} {detail}"
        super().__init__(msg)
