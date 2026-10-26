from common.core.domain import IErrorCode


class ScholarErrorCode(IErrorCode):
    CAPABILITY_UNAVAILABLE = (503, "Requested scholar capability is unavailable")
