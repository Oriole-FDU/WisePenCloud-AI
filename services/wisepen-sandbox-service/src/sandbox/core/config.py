from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SandboxRuntimeConfig:
    provider: str = "docker"
    allowed_script_types: List[str] = field(default_factory=lambda: ["python", "bat"])
    env_allowlist: List[str] = field(default_factory=list)
    output_max_chars: int = 4000
    default_timeout_ms: int = 60_000
    default_network_enabled: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)


class ConfigLoader:
    def load(self) -> SandboxRuntimeConfig:
        raise NotImplementedError

