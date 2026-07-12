class SandboxDomainError(Exception):
    code = "SANDBOX_UNAVAILABLE"


class PoolEmptyError(SandboxDomainError):
    code = "POOL_EMPTY"


class LeaseNotFoundError(SandboxDomainError):
    code = "LEASE_NOT_FOUND"


class LeaseConflictError(SandboxDomainError):
    code = "REQUEST_CONFLICT"


class InvalidStateTransition(SandboxDomainError):
    code = "INVALID_STATE_TRANSITION"


class LeaseExpiredError(SandboxDomainError):
    code = "LEASE_EXPIRED"


class FencingRejectedError(SandboxDomainError):
    code = "FENCING_REJECTED"


class WorkspaceSyncError(SandboxDomainError):
    code = "WORKSPACE_SYNC_FAILED"


class SandboxUnavailableError(SandboxDomainError):
    code = "SANDBOX_UNAVAILABLE"
