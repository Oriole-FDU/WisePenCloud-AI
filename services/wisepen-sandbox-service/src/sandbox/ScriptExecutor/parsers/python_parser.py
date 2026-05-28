from __future__ import annotations

from sandbox.ScriptExecutor.scriptReader import ScriptPackage, ScriptParser, ScriptSpec, ScriptType
from sandbox.core.errors import SandboxError, SandboxErrorCode


class PythonScriptParser(ScriptParser):
    def can_parse(self, package: ScriptPackage) -> bool:
        for f in package.files:
            if f.file_name.lower().endswith(".py"):
                return True
        return False

    def parse(self, package: ScriptPackage) -> ScriptSpec:
        py_files = [f.file_name for f in package.files if f.file_name.lower().endswith(".py")]
        if not py_files:
            raise SandboxError(
                code=SandboxErrorCode.UNSUPPORTED_SCRIPT,
                message="python parser: no .py file found",
            )

        entry_candidates = ["main.py", "app.py", "run.py", "__main__.py"]
        normalized = {p.replace("\\", "/").split("/")[-1].lower(): p for p in py_files}

        if len(py_files) == 1 and len(list(package.files)) == 1:
            entry = py_files[0].replace("\\", "/")
        else:
            entry = None
            for c in entry_candidates:
                if c in normalized:
                    entry = normalized[c].replace("\\", "/")
                    break
            if entry is None:
                raise SandboxError(
                    code=SandboxErrorCode.VALIDATION_FAILED,
                    message="python parser: entry is required for multi-file package",
                    detail="provide entry (e.g. main.py) in structured_request",
                )

        return ScriptSpec(
            script_type=ScriptType.PYTHON,
            entry=entry,
            args=[],
            env={},
            files=list(package.files),
            working_dir=package.root_dir or ".",
        )
