class OcrError(Exception):
    pass


class OcrProcessingError(OcrError):
    pass


class OcrWorkerError(OcrProcessingError):
    pass
