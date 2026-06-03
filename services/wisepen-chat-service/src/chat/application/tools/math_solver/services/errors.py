class MathSolverError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        self.message = message
        self.retryable = retryable
        super().__init__(message)
