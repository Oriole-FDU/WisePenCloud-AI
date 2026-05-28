from __future__ import annotations

from typing import List

from sandbox.ScriptExecutor.execution.runners.base import Runner
from sandbox.ScriptExecutor.scriptExecutor import SandboxSpec
from sandbox.ScriptExecutor.scriptReader import ScriptSpec


class BatRunner(Runner):
    def build_command(self, script: ScriptSpec, sandbox: SandboxSpec) -> List[str]:
        args = ["cmd", "/c", script.entry]
        args.extend(script.args or [])
        return args

