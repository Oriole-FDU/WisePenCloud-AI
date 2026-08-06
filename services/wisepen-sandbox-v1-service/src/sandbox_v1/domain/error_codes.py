from common.core.domain import IErrorCode


class SandboxErrorCode(IErrorCode):
    """Error codes used by the container-pool core."""

    POOL_EMPTY = (46001, "sandbox pool has no READY container")
    INVALID_CONSUME_REQUEST = (46005, "consume request identifiers are required")
    INVALID_STATE_TRANSITION = (46006, "invalid sandbox state transition")
    SANDBOX_UNAVAILABLE = (46009, "sandbox service is temporarily unavailable")
    USER_SANDBOX_CAPACITY = (46014, "user sandbox capacity has been reached")
    INVALID_WORKSPACE_REQUEST = (46101, "workspace request identifiers are required")
    WORKSPACE_SNAPSHOT_REJECTED = (
        46102,
        "workspace snapshot contains unsupported files",
    )
    WORKSPACE_PATH_UNSAFE = (46103, "workspace path is outside the managed root")
