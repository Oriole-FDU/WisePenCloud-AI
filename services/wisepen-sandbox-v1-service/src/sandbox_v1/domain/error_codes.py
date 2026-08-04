from common.core.domain import IErrorCode


class SandboxErrorCode(IErrorCode):
    """Error codes used by the container-pool core."""

    POOL_EMPTY = (46001, "sandbox pool has no READY container")
    INVALID_CONSUME_REQUEST = (46005, "consume request identifiers are required")
    INVALID_STATE_TRANSITION = (46006, "invalid sandbox state transition")
    SANDBOX_UNAVAILABLE = (46009, "sandbox service is temporarily unavailable")
    USER_SANDBOX_CAPACITY = (46014, "user sandbox capacity has been reached")
