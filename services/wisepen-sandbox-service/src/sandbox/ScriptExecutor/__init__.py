from sandbox.core.lazy import make_getattr

__all__ = [
    "BatRunner", "BatScriptParser", "DefaultExecutionRequestParser",
    "DefaultScriptParserFactory", "DefaultScriptsExecutor",
    "ExecutionArtifact", "ExecutionContext", "ExecutionRequest",
    "ExecutionResult", "ExecutionStatus", "InputScripts",
    "LocalFsScriptPackageRepository", "PythonRunner", "PythonScriptParser",
    "ResultSinkSpec", "SandboxExecutionService", "SandboxSpec",
    "ScriptFile", "ScriptPackage", "ScriptPackageRepository",
    "ScriptParser", "ScriptParserFactory", "ScriptSpec",
    "ScriptType", "ScriptsExecutor",
]

_LAZY = {
    "ExecutionArtifact": "sandbox.ScriptExecutor.scriptExecutor",
    "ExecutionContext": "sandbox.ScriptExecutor.scriptExecutor",
    "ExecutionRequest": "sandbox.ScriptExecutor.scriptExecutor",
    "ExecutionResult": "sandbox.ScriptExecutor.scriptExecutor",
    "ExecutionStatus": "sandbox.ScriptExecutor.scriptExecutor",
    "ResultSinkSpec": "sandbox.ScriptExecutor.scriptExecutor",
    "SandboxExecutionService": "sandbox.ScriptExecutor.scriptExecutor",
    "SandboxSpec": "sandbox.ScriptExecutor.scriptExecutor",
    "ScriptsExecutor": "sandbox.ScriptExecutor.scriptExecutor",
    "InputScripts": "sandbox.ScriptExecutor.scriptReader",
    "ScriptFile": "sandbox.ScriptExecutor.scriptReader",
    "ScriptPackage": "sandbox.ScriptExecutor.scriptReader",
    "ScriptPackageRepository": "sandbox.ScriptExecutor.scriptReader",
    "ScriptParser": "sandbox.ScriptExecutor.scriptReader",
    "ScriptParserFactory": "sandbox.ScriptExecutor.scriptReader",
    "ScriptSpec": "sandbox.ScriptExecutor.scriptReader",
    "ScriptType": "sandbox.ScriptExecutor.scriptReader",
    "DefaultExecutionRequestParser": "sandbox.ScriptExecutor.execution.request_parser",
    "DefaultScriptsExecutor": "sandbox.ScriptExecutor.execution.executor_impl",
    "DefaultScriptParserFactory": "sandbox.ScriptExecutor.parsers.factory",
    "PythonScriptParser": "sandbox.ScriptExecutor.parsers.python_parser",
    "BatScriptParser": "sandbox.ScriptExecutor.parsers.bat_parser",
    "PythonRunner": "sandbox.ScriptExecutor.execution.runners.python_runner",
    "BatRunner": "sandbox.ScriptExecutor.execution.runners.bat_runner",
    "LocalFsScriptPackageRepository": "sandbox.ScriptExecutor.package_repo.local_fs_repo",
}

__getattr__ = make_getattr(_LAZY)
