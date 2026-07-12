class SandboxDomainError(Exception):
    pass


class PoolEmptyError(SandboxDomainError):
    pass


class LeaseNotFoundError(SandboxDomainError):
    pass


class InvalidStateTransition(SandboxDomainError):
    pass


class LeaseExpiredError(SandboxDomainError):
    pass
