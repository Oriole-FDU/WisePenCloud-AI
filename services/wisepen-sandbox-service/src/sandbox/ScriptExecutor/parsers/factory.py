from __future__ import annotations

from sandbox.ScriptExecutor.scriptReader import ScriptPackage, ScriptParser, ScriptParserFactory
from sandbox.core.errors import SandboxError, SandboxErrorCode


class DefaultScriptParserFactory(ScriptParserFactory):
    def register(self, parser: ScriptParser) -> None:
        self._parsers.append(parser)

    def get_parser(self, package: ScriptPackage) -> ScriptParser:
        for p in self._parsers:
            try:
                if p.can_parse(package):
                    return p
            except Exception:
                continue
        raise SandboxError(
            code=SandboxErrorCode.UNSUPPORTED_SCRIPT,
            message="no script parser can handle this package",
        )
