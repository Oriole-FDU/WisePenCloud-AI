from __future__ import annotations

from typing import Any

__all__ = [
    "BatRunner",
    "BatScriptParser",
    "DefaultExecutionRequestParser",
    "DefaultScriptParserFactory",
    "DefaultScriptsExecutor",
    "ExecutionArtifact",
    "ExecutionContext",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "InputScripts",
    "LocalFsScriptPackageRepository",
    "PythonRunner",
    "PythonScriptParser",
    "ResultSinkSpec",
    "SandboxExecutionService",
    "SandboxSpec",
    "ScriptFile",
    "ScriptPackage",
    "ScriptPackageRepository",
    "ScriptParser",
    "ScriptParserFactory",
    "ScriptSpec",
    "ScriptType",
    "ScriptsExecutor",
]


def __getattr__(name: str) -> Any:
    if name in (
        "ExecutionArtifact",
        "ExecutionContext",
        "ExecutionRequest",
        "ExecutionResult",
        "ExecutionStatus",
        "ResultSinkSpec",
        "SandboxExecutionService",
        "SandboxSpec",
        "ScriptsExecutor",
    ):
        from sandbox.ScriptExecutor.scriptExecutor import (
            ExecutionArtifact,
            ExecutionContext,
            ExecutionRequest,
            ExecutionResult,
            ExecutionStatus,
            ResultSinkSpec,
            SandboxExecutionService,
            SandboxSpec,
            ScriptsExecutor,
        )

        return locals()[name]

    if name in (
        "InputScripts",
        "ScriptFile",
        "ScriptPackage",
        "ScriptPackageRepository",
        "ScriptParser",
        "ScriptParserFactory",
        "ScriptSpec",
        "ScriptType",
    ):
        from sandbox.ScriptExecutor.scriptReader import (
            InputScripts,
            ScriptFile,
            ScriptPackage,
            ScriptPackageRepository,
            ScriptParser,
            ScriptParserFactory,
            ScriptSpec,
            ScriptType,
        )

        return locals()[name]

    if name == "DefaultExecutionRequestParser":
        from sandbox.ScriptExecutor.execution.request_parser import DefaultExecutionRequestParser

        return DefaultExecutionRequestParser

    if name == "DefaultScriptsExecutor":
        from sandbox.ScriptExecutor.execution.executor_impl import DefaultScriptsExecutor

        return DefaultScriptsExecutor

    if name == "DefaultScriptParserFactory":
        from sandbox.ScriptExecutor.parsers.factory import DefaultScriptParserFactory

        return DefaultScriptParserFactory

    if name == "PythonScriptParser":
        from sandbox.ScriptExecutor.parsers.python_parser import PythonScriptParser

        return PythonScriptParser

    if name == "BatScriptParser":
        from sandbox.ScriptExecutor.parsers.bat_parser import BatScriptParser

        return BatScriptParser

    if name == "PythonRunner":
        from sandbox.ScriptExecutor.execution.runners.python_runner import PythonRunner

        return PythonRunner

    if name == "BatRunner":
        from sandbox.ScriptExecutor.execution.runners.bat_runner import BatRunner

        return BatRunner

    if name == "LocalFsScriptPackageRepository":
        from sandbox.ScriptExecutor.package_repo.local_fs_repo import LocalFsScriptPackageRepository

        return LocalFsScriptPackageRepository

    raise AttributeError(name)
