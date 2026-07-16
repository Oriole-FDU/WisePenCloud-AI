class AdapterError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ContainerError(AdapterError):
    pass


class AioRequestError(AdapterError):
    pass


class AioNotFoundError(AioRequestError):
    pass
