from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, Field


class LLMVisibility(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class PersistenceMode(str, Enum):
    PERSIST_FULL = "persist_full"
    DROP = "drop"
    REDACT_CONTENT = "redact_content"
    PERSIST_CONTENT = "persist_content"


class RestoreRef(BaseModel):
    kind: str
    data: Dict[str, str] = Field(default_factory=dict)


class MessageLifecycle(BaseModel):
    llm_visibility: LLMVisibility = LLMVisibility.INCLUDE
    persistence_mode: PersistenceMode = PersistenceMode.PERSIST_FULL
    restore_ref: Optional[RestoreRef] = None
