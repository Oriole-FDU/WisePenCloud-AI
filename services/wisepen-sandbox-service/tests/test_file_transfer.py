from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from common.core.exceptions import ServiceException

from sandbox.core.providers.aio_adapter.file_transfer import DockerWorkspaceTransfer
from sandbox.domain.entities import SandboxRef, WorkspaceSnapshot
from sandbox.domain.error_codes import SandboxErrorCode


@pytest.mark.asyncio
async def test_copy_in_stages_before_atomic_workspace_swap(monkeypatch):
    transfer = DockerWorkspaceTransfer(workspace_root="/home/gem/workspaces")
    calls: list[list[str]] = []
    copied_content = b""

    def run(args: list[str]) -> str:
        nonlocal copied_content
        calls.append(args)
        if args[0] == "cp":
            copied_content = (Path(args[1].removesuffix("/.")) / "main.py").read_bytes()
        return ""

    monkeypatch.setattr(transfer, "_run", run)
    await transfer.copy_in(
        SandboxRef("sb-1", "container-1"),
        WorkspaceSnapshot("tenant", "workspace", {"main.py": b"\x00python"}),
    )

    assert copied_content == b"\x00python"
    assert not any(
        call[:5] == ["exec", "container-1", "rm", "-rf", "/home/gem/workspaces/tenant/workspace"]
        for call in calls
    )
    assert any(call[0] == "cp" for call in calls)
    assert any(call[:4] == ["exec", "container-1", "sh", "-c"] for call in calls)


@pytest.mark.asyncio
async def test_copy_in_cp_failure_never_runs_swap(monkeypatch):
    transfer = DockerWorkspaceTransfer()
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        if args[0] == "cp":
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_SYNC_FAILED, "copy failed"
            )
        return ""

    monkeypatch.setattr(transfer, "_run", run)
    monkeypatch.setattr(transfer, "_run_cleanup", lambda args: calls.append(args))
    with pytest.raises(ServiceException) as exc_info:
        await transfer.copy_in(
            SandboxRef("sb-1", "container-1"),
            WorkspaceSnapshot("tenant", "workspace", {"main.py": "old-safe"}),
        )
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_SYNC_FAILED.code
    assert not any(call[:4] == ["exec", "container-1", "sh", "-c"] for call in calls)


@pytest.mark.asyncio
async def test_empty_copy_in_still_replaces_workspace(monkeypatch):
    transfer = DockerWorkspaceTransfer()
    calls: list[list[str]] = []
    monkeypatch.setattr(transfer, "_run", lambda args: calls.append(args) or "")

    await transfer.copy_in(
        SandboxRef("sb-1", "container-1"),
        WorkspaceSnapshot("tenant", "workspace"),
    )

    assert not any(call[0] == "cp" for call in calls)
    assert any(call[:4] == ["exec", "container-1", "sh", "-c"] for call in calls)


@pytest.mark.asyncio
async def test_copy_out_returns_text_and_binary_complete_snapshot(monkeypatch):
    transfer = DockerWorkspaceTransfer()

    def run_result(args):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run(args: list[str]) -> str:
        if args[0] == "cp":
            destination = Path(args[-1])
            (destination / "dir").mkdir(parents=True)
            (destination / "dir" / "result.txt").write_text("done", encoding="utf-8")
            (destination / "blob.bin").write_bytes(b"\xff\x00")
        return ""

    monkeypatch.setattr(transfer, "_run_result", run_result)
    monkeypatch.setattr(transfer, "_run", run)
    snapshot = await transfer.copy_out(
        SandboxRef("sb-1", "container-1"), "tenant", "workspace"
    )

    assert snapshot.files == {"blob.bin": b"\xff\x00", "dir/result.txt": "done"}


@pytest.mark.asyncio
async def test_copy_out_missing_workspace_is_empty(monkeypatch):
    transfer = DockerWorkspaceTransfer()
    monkeypatch.setattr(
        transfer,
        "_run_result",
        lambda args: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    snapshot = await transfer.copy_out(
        SandboxRef("sb-1", "container-1"), "tenant", "workspace"
    )
    assert snapshot.files == {}


@pytest.mark.asyncio
async def test_copy_out_docker_failure_is_not_treated_as_missing(monkeypatch):
    transfer = DockerWorkspaceTransfer()
    monkeypatch.setattr(
        transfer,
        "_run_result",
        lambda args: SimpleNamespace(
            returncode=1, stdout="", stderr="docker daemon unavailable"
        ),
    )
    with pytest.raises(ServiceException) as exc_info:
        await transfer.copy_out(
            SandboxRef("sb-1", "container-1"), "tenant", "workspace"
        )
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_SYNC_FAILED.code


@pytest.mark.asyncio
async def test_copy_out_rejects_symbolic_links(monkeypatch):
    transfer = DockerWorkspaceTransfer()
    monkeypatch.setattr(
        transfer,
        "_run_result",
        lambda args: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    def run(args):
        destination = Path(args[-1])
        (destination / "target").write_text("secret")
        (destination / "link").symlink_to(destination / "target")
        return ""

    monkeypatch.setattr(transfer, "_run", run)
    with pytest.raises(ServiceException) as exc_info:
        await transfer.copy_out(
            SandboxRef("sb-1", "container-1"), "tenant", "workspace"
        )
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code


@pytest.mark.asyncio
async def test_checkpoint_requires_lease_and_fencing():
    transfer = DockerWorkspaceTransfer()
    sandbox = SandboxRef("sb-1", "container-1")

    with pytest.raises(ServiceException) as exc_info:
        await transfer.checkpoint(sandbox, "tenant", "workspace", "", 0)

    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code


@pytest.mark.asyncio
async def test_copy_in_rejects_path_escape_and_limits():
    sandbox = SandboxRef("sb-1", "container-1")
    transfer = DockerWorkspaceTransfer(max_file_bytes=2)
    with pytest.raises(ServiceException) as path_error:
        await transfer.copy_in(
            sandbox,
            WorkspaceSnapshot("tenant", "workspace", {"../secret": "x"}),
        )
    assert path_error.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code
    with pytest.raises(ServiceException) as limit_error:
        await transfer.copy_in(
            sandbox,
            WorkspaceSnapshot("tenant", "workspace", {"large": "abc"}),
        )
    assert limit_error.value.code == SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED.code
