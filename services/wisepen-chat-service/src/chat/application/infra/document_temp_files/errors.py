class DocumentFileError(Exception):
    pass


class InvalidDocumentRefError(DocumentFileError):
    pass


class UnreadableDocumentRefError(DocumentFileError):
    pass
