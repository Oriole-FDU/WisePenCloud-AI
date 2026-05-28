from __future__ import annotations

from sandbox.ScriptExecutor.scriptReader import ScriptPackage, ScriptParser, ScriptSpec, ScriptType
from sandbox.core.errors import SandboxError, SandboxErrorCode


class BatScriptParser(ScriptParser):
    def can_parse(self, package: ScriptPackage) -> bool:
        for f in package.files:
            n = f.file_name.lower()
            if n.endswith(".bat") or n.endswith(".cmd"):
                return True
        return False

    def parse(self, package: ScriptPackage) -> ScriptSpec:
        bat_files = [f.file_name for f in package.files if f.file_name.lower().endswith((".bat", ".cmd"))]
        if not bat_files:
            raise SandboxError(
                code=SandboxErrorCode.UNSUPPORTED_SCRIPT,
                message="bat parser: no .bat/.cmd file found",
            )

        if len(bat_files) == 1 and len(list(package.files)) == 1:
            entry = bat_files[0].replace("\\", "/")
        else:
            entry_candidates = ["main.bat", "run.bat", "app.bat", "main.cmd", "run.cmd", "app.cmd"]
            normalized = {p.replace("\\", "/").split("/")[-1].lower(): p for p in bat_files}
            entry = None
            for c in entry_candidates:
                if c in normalized:
                    entry = normalized[c].replace("\\", "/")
                    break
            if entry is None:
                raise SandboxError(
                    code=SandboxErrorCode.VALIDATION_FAILED,
                    message="bat parser: entry is required for multi-file package",
                    detail="provide entry (e.g. main.bat) in structured_request",
                )

        return ScriptSpec(
            script_type=ScriptType.BAT,
            entry=entry,
            args=[],
            env={},
            files=list(package.files),
            working_dir=package.root_dir or ".",
        )
