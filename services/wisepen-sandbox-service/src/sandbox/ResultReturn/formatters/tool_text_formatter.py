from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from sandbox.ScriptExecutor.scriptExecutor import ExecutionResult


class ToolTextFormatter(ABC):
    @abstractmethod
    def format(self, result: ExecutionResult) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class ToolTextFormatterConfig:
    max_chars: int = 4000
    include_stderr: bool = True
    include_stdout: bool = True


class DefaultToolTextFormatter(ToolTextFormatter):
    def __init__(self, config: Optional[ToolTextFormatterConfig] = None) -> None:
        self._config = config or ToolTextFormatterConfig()

    def format(self, result: ExecutionResult) -> str:
        lines: List[str] = ["[Sandbox Execution]"]
        lines.append(f"status: {result.status.value}")
        if result.sandbox_id is not None:
            lines.append(f"sandbox_id: {result.sandbox_id}")
        if result.exit_code is not None:
            lines.append(f"exit_code: {result.exit_code}")
        if result.duration_ms is not None:
            lines.append(f"duration_ms: {result.duration_ms}")

        if self._config.include_stdout:
            lines.append("stdout:")
            lines.append(self._truncate(result.stdout or ""))
        if self._config.include_stderr:
            lines.append("stderr:")
            lines.append(self._truncate(result.stderr or ""))

        artifacts = result.artifacts or []
        if artifacts:
            lines.append("artifacts:")
            for a in artifacts:
                lines.append(f"- name={a.name} uri={a.uri}")

        content = "\n".join(lines).strip() + "\n"
        if self._config.max_chars > 0 and len(content) > self._config.max_chars:
            content = content[: self._config.max_chars] + "\n...[truncated]\n"
        return content

    def _truncate(self, text: str) -> str:
        max_chars = self._config.max_chars
        if max_chars <= 0:
            return text
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[truncated]"
