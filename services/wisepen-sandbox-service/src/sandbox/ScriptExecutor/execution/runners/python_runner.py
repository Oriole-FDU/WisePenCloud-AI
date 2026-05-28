from __future__ import annotations

from typing import List

from sandbox.ScriptExecutor.execution.runners.base import Runner
from sandbox.ScriptExecutor.scriptExecutor import SandboxSpec
from sandbox.ScriptExecutor.scriptReader import ScriptSpec


class PythonRunner(Runner):
    def build_command(self, script: ScriptSpec, sandbox: SandboxSpec) -> List[str]:
        args = ["python", "-I", script.entry]
        args.extend(script.args or [])
        return args
