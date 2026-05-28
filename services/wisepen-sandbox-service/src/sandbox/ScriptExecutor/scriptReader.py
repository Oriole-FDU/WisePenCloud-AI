# Read and parse input scripts
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import os
from typing import Dict, List, Optional, Sequence

from sandbox.core.errors import SandboxError, SandboxErrorCode


class ScriptType(str, Enum):
    PYTHON = "python"
    BAT = "bat"
    SHELL = "shell"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ScriptFile:
    file_name: str
    content: bytes


@dataclass(frozen=True)
class ScriptPackage:
    files: Sequence[ScriptFile]
    package_id: Optional[str] = None
    root_dir: str = "."


@dataclass(frozen=True)
class ScriptSpec:
    script_type: ScriptType
    entry: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    files: List[ScriptFile] = field(default_factory=list)
    working_dir: str = "."


class ScriptParser(ABC):
    @abstractmethod
    def can_parse(self, package: ScriptPackage) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse(self, package: ScriptPackage) -> ScriptSpec:
        raise NotImplementedError


class ScriptParserFactory:
    def __init__(self) -> None:
        self._parsers: List[ScriptParser] = []

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


class ScriptPackageRepository(ABC):
    @abstractmethod
    def get(self, package_id: str) -> ScriptPackage:
        raise NotImplementedError

    @abstractmethod
    def put(self, package: ScriptPackage) -> str:
        raise NotImplementedError


class InputScripts:
    def __init__(self, parser_factory: ScriptParserFactory) -> None:
        self._parser_factory = parser_factory

    def readFile(self, fileName: str) -> ScriptFile:
        if not fileName:
            raise SandboxError(code=SandboxErrorCode.VALIDATION_FAILED, message="fileName is required")
        if not os.path.isfile(fileName):
            raise SandboxError(
                code=SandboxErrorCode.VALIDATION_FAILED,
                message="script file not found",
                detail=fileName,
            )
        with open(fileName, "rb") as f:
            content = f.read()
        return ScriptFile(file_name=os.path.basename(fileName), content=content)

    def parseFile(
        self,
        fileName: Optional[str] = None,
        files: Optional[Sequence[ScriptFile]] = None,
        package_id: Optional[str] = None,
        root_dir: str = ".",
    ) -> ScriptSpec:
        resolved_files: List[ScriptFile] = []
        if files is not None:
            resolved_files = list(files)
        elif fileName is not None:
            resolved_files = [self.readFile(fileName)]

        if not resolved_files:
            raise SandboxError(
                code=SandboxErrorCode.VALIDATION_FAILED,
                message="either fileName or files must be provided",
            )

        package = ScriptPackage(files=resolved_files, package_id=package_id, root_dir=root_dir or ".")
        parser = self._parser_factory.get_parser(package)
        return parser.parse(package)
