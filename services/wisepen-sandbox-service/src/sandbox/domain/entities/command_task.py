from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


class CommandTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"
    LOST = "lost"


class CommandTaskDocument(Document):
    """Sandbox-service 命令任务及其结果"""

    task_id: str = Field(..., description="命令任务 ID")
    user_id: str
    session_id: str
    sandbox_id: str | None = None
    workspace_id: str | None = None
    provider_id: str | None = None
    command: str
    working_directory: str | None = None
    timeout_seconds: int

    status: CommandTaskStatus = CommandTaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None

    provider_session_id: str | None = None
    provider_command_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    exit_code: int | None = None
    error: str | None = None

    class Settings:
        name = "wisepen_sandbox_command_task"
        indexes = [
            IndexModel([("task_id", ASCENDING)], unique=True, name="uniq_command_task_id"),
            IndexModel([("status", ASCENDING), ("created_at", ASCENDING)], name="idx_command_task_status"),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="idx_command_task_user_created"),
        ]