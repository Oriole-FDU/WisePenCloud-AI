from contextvars import ContextVar

_gray_context: ContextVar[str] = ContextVar("gray_context", default="")


class GrayContextHolder:
    @staticmethod
    def set_developer_tag(tag: str):
        _gray_context.set(tag)

    @staticmethod
    def get_developer_tag() -> str:
        return _gray_context.get()
