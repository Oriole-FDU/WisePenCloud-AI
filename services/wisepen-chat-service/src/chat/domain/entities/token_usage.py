from enum import Enum


class TokenUsageSource(str, Enum):
    PROVIDER = "provider"
    ESTIMATED_SPLIT = "estimated_split"
    ESTIMATED = "estimated"
    MIXED = "mixed"
