class FileHandoffError(Exception):
    pass


class FileHandoffWriteError(FileHandoffError):
    pass


class FileHandoffInvalidSuffixError(FileHandoffError):
    def __init__(self, suffix: str):
        self.suffix = suffix
        super().__init__(f"Unsupported handoff file suffix: {suffix}")
