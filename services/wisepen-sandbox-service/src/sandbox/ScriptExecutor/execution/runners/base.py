from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from sandbox.ScriptExecutor.scriptExecutor import SandboxSpec
from sandbox.ScriptExecutor.scriptReader import ScriptSpec


class Runner(ABC):
    @abstractmethod
    def build_command(self, script: ScriptSpec, sandbox: SandboxSpec) -> List[str]:
        raise NotImplementedError

