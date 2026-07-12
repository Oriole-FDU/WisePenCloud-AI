from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class SpeechCredential:
    provider: str
    expires_at: datetime
    credential: dict[str, Any]


class SpeechProvider(ABC):
    @abstractmethod
    def get_recognition_credential(self, options: Mapping[str, Any]) -> SpeechCredential:
        pass
