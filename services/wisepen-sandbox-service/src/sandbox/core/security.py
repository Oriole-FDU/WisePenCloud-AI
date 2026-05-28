from __future__ import annotations

from typing import Dict, List


class EnvPolicy:
    def __init__(self, allowlist: List[str]) -> None:
        self._allowlist = allowlist

    def filter(self, env: Dict[str, str]) -> Dict[str, str]:
        raise NotImplementedError


class ArgumentPolicy:
    def validate_args(self, args: List[str]) -> None:
        raise NotImplementedError

