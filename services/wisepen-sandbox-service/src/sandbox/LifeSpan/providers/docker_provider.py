from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sandbox.core.errors import SandboxError, SandboxErrorCode
from sandbox.LifeSpan.sandboxLifespan import DockerSandboxProvider, SandboxCreateRequest, SandboxInfo, SandboxState

_DEBUG = (os.getenv("SANDBOX_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(event: str, **fields: object) -> None:
    if not _DEBUG:
        return
    try:
        payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        payload = str(fields)
    print(f"[SANDBOX][docker_provider] {event} | {payload}")


class DockerSandboxProviderImpl(DockerSandboxProvider):
    def __init__(
        self,
        docker_bin: str = "docker",
        default_image: str = "python:3.11-slim",
        default_workdir: str = "/workspace",
    ) -> None:
        self._docker_bin = docker_bin
        self._default_image = default_image
        self._default_workdir = default_workdir

    def create(self, request: SandboxCreateRequest) -> SandboxInfo:
        image = (
            str(request.metadata.get("image"))
            if request.metadata and request.metadata.get("image")
            else (request.runtime or self._default_image)
        )
        workdir = (
            str(request.metadata.get("workdir"))
            if request.metadata and request.metadata.get("workdir")
            else self._default_workdir
        )
        name_prefix = (
            str(request.metadata.get("name_prefix"))
            if request.metadata and request.metadata.get("name_prefix")
            else "wisepen-sandbox"
        )
        container_name = f"{name_prefix}-{request.request_id[:8]}-{uuid.uuid4().hex[:8]}"
        command = request.metadata.get("command") if request.metadata else None
        if isinstance(command, list):
            cmd_tail: List[str] = [str(x) for x in command]
        elif isinstance(command, str) and command.strip():
            cmd_tail = ["sh", "-c", command]
        else:
            cmd_tail = ["sh", "-c", "while true; do sleep 3600; done"]

        args: List[str] = [
            "run",
            "-d",
            "--name",
            container_name,
            "--label",
            "wisepen.managed=true",
            "--label",
            f"wisepen.request_id={request.request_id}",
            "-w",
            workdir,
        ]
        _dbg(
            "create_begin",
            request_id=request.request_id,
            image=image,
            workdir=workdir,
            container_name=container_name,
            cpu_cores=request.limits.cpu_cores,
            memory_mb=request.limits.memory_mb,
            pids_limit=request.limits.pids_limit,
            network_enabled=request.limits.network_enabled,
        )

        if request.limits.cpu_cores is not None:
            args += ["--cpus", str(request.limits.cpu_cores)]
        if request.limits.memory_mb is not None:
            args += ["--memory", f"{int(request.limits.memory_mb)}m"]
        if request.limits.pids_limit is not None:
            args += ["--pids-limit", str(int(request.limits.pids_limit))]
        if request.limits.network_enabled is False:
            args += ["--network", "none"]

        for k, v in (request.env or {}).items():
            args += ["-e", f"{k}={v}"]

        args += [image]
        args += cmd_tail

        _dbg("docker_run", request_id=request.request_id, args=args[:12], image=image)
        container_id = self._run_docker(args).strip()
        if not container_id:
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message="docker run returned empty container id",
            )
        _dbg("create_ok", request_id=request.request_id, sandbox_id=container_id)
        return self.info(container_id)

    def remove(self, sandbox_id: str) -> None:
        _dbg("remove_begin", sandbox_id=sandbox_id)
        try:
            self._run_docker(["rm", "-f", sandbox_id])
            _dbg("remove_ok", sandbox_id=sandbox_id)
        except SandboxError as e:
            if e.detail and "No such container" in e.detail:
                _dbg("remove_not_found", sandbox_id=sandbox_id)
                return
            _dbg("remove_failed", sandbox_id=sandbox_id, detail=e.detail)
            raise

    def info(self, sandbox_id: str) -> SandboxInfo:
        _dbg("info_begin", sandbox_id=sandbox_id)
        raw = self._run_docker(["inspect", sandbox_id])
        try:
            items = json.loads(raw)
            item = items[0] if isinstance(items, list) and items else None
        except json.JSONDecodeError as e:
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message="docker inspect output is not valid json",
                detail=str(e),
            )
        if not isinstance(item, dict):
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message="docker inspect returned empty result",
                detail=raw[:500],
            )

        state = self._map_state(item.get("State") or {})
        created_at_epoch_ms = self._parse_epoch_ms(item.get("Created"))
        image = item.get("Config", {}).get("Image") if isinstance(item.get("Config"), dict) else None
        workdir = item.get("Config", {}).get("WorkingDir") if isinstance(item.get("Config"), dict) else None
        _dbg(
            "info_ok",
            sandbox_id=str(item.get("Id") or sandbox_id),
            state=state.value,
            image=str(image) if image else None,
            workdir=workdir or self._default_workdir,
        )
        return SandboxInfo(
            sandbox_id=str(item.get("Id") or sandbox_id),
            state=state,
            created_at_epoch_ms=created_at_epoch_ms,
            last_used_at_epoch_ms=None,
            workspace=workdir or self._default_workdir,
            runtime=str(image) if image else None,
            provider="docker",
            limits=None,
            metadata={
                "name": str(item.get("Name") or "").lstrip("/"),
                "image": str(image) if image else "",
            },
        )

    def _run_docker(self, args: List[str]) -> str:
        try:
            completed = subprocess.run(
                [self._docker_bin, *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message="docker binary not found",
                detail=str(e),
            )
        except Exception as e:
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message="failed to execute docker command",
                detail=str(e),
            )

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message=f"docker command failed: {' '.join(args[:2])}",
                detail=detail[:2000] if detail else None,
            )
        return (completed.stdout or "").strip()

    def _map_state(self, state: Dict[str, Any]) -> SandboxState:
        status = str(state.get("Status") or "").lower()
        if status in {"running", "restarting"}:
            return SandboxState.RUNNING
        if status in {"created", "paused"}:
            return SandboxState.IDLE
        if status in {"removing"}:
            return SandboxState.DELETING
        if status in {"exited", "dead"}:
            return SandboxState.FAILED
        return SandboxState.FAILED

    def _parse_epoch_ms(self, value: Optional[str]) -> Optional[int]:
        if not value or not isinstance(value, str):
            return None
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if "." in s:
            head, tail = s.split(".", 1)
            frac = tail
            tz = ""
            if "+" in tail:
                frac, tz = tail.split("+", 1)
                tz = "+" + tz
            elif "-" in tail[6:]:
                frac, tz = tail.rsplit("-", 1)
                tz = "-" + tz
            frac = frac[:6]
            s = f"{head}.{frac}{tz}"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
