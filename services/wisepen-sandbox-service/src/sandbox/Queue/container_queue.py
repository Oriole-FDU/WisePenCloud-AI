"""
Container queue — manages a pool of AIO sandbox containers.

States: idle → busy → dirty → (recycle) → idle
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

_DEBUG = (os.getenv("SANDBOX_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(event: str, **fields: object) -> None:
    if not _DEBUG:
        return
    try:
        payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        payload = str(fields)
    print(f"[SANDBOX][queue] {event} | {payload}")


class ContainerState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    DIRTY = "dirty"
    DEAD = "dead"


@dataclass
class ContainerInfo:
    container_id: str
    container_name: str
    state: ContainerState = ContainerState.IDLE
    user_id: str = ""
    session_id: str = ""
    allocated_at: float = 0.0
    created_at: float = field(default_factory=time.time)


class ContainerQueue:
    """Thread-safe pool of AIO containers with acquire/release/recycle lifecycle."""

    def __init__(
        self,
        image: str = "ghcr.io/agent-infra/sandbox:latest",
        min_idle: int = 2,
        max_total: int = 8,
        workspace_cache: str = "/workspaces",
    ):
        self._image = image
        self._min_idle = min_idle
        self._max_total = max_total
        self._workspace_cache = workspace_cache
        self._containers: dict[str, ContainerInfo] = {}
        self._lock = threading.Lock()

    # ---- public API ----

    def acquire(self, user_id: str, session_id: str) -> str:
        """
        Get an idle container for the given tenant.
        Blocks briefly if no idle containers available (prefetch triggers in bg).
        Raises RuntimeError if no container available within timeout.
        """
        with self._lock:
            # Try direct idle container
            idle = [c for c in self._containers.values() if c.state == ContainerState.IDLE]
            if idle:
                info = idle[0]
                info.state = ContainerState.BUSY
                info.user_id = user_id
                info.session_id = session_id
                info.allocated_at = time.time()
                _dbg("acquired", cid=info.container_id[:12], user=user_id, session=session_id)
                return info.container_id

            # No idle — try immediate prefetch if under max
            if len(self._containers) < self._max_total:
                _dbg("acquire_no_idle_prefetch", total=len(self._containers))
                cid = self._start_container()
                info = ContainerInfo(
                    container_id=cid,
                    container_name=f"aio-worker-{uuid.uuid4().hex[:8]}",
                    state=ContainerState.BUSY,
                    user_id=user_id,
                    session_id=session_id,
                    allocated_at=time.time(),
                )
                self._containers[cid] = info
                _dbg("acquired_new", cid=cid[:12], user=user_id, session=session_id)
                return cid

        raise RuntimeError(
            f"no idle containers available (total={len(self._containers)}, max={self._max_total})"
        )

    def release(self, container_id: str) -> None:
        """Release a busy container back to the pool (marks dirty for recycling)."""
        with self._lock:
            info = self._containers.get(container_id)
            if not info:
                _dbg("release_unknown", cid=container_id[:12])
                return
            info.state = ContainerState.DIRTY
            info.user_id = ""
            info.session_id = ""
            _dbg("released", cid=container_id[:12])

    def recycle(self, container_id: str) -> str | None:
        """
        Clean a dirty container: destroy and recreate, return new container_id.
        Returns None if container not found or not dirty.
        """
        with self._lock:
            info = self._containers.get(container_id)
            if not info or info.state != ContainerState.DIRTY:
                return None

            _dbg("recycle_start", cid=container_id[:12])
            # Destroy old container
            self._rm_container(container_id)
            old_name = info.container_name

            # Create replacement
            new_cid = self._start_container()
            info.container_id = new_cid
            info.container_name = old_name
            info.state = ContainerState.IDLE
            info.allocated_at = 0.0

            # Update key since container_id changed
            del self._containers[container_id]
            self._containers[new_cid] = info
            _dbg("recycled", old_cid=container_id[:12], new_cid=new_cid[:12])
            return new_cid

    def ensure_idle_count(self) -> int:
        """Ensure at least min_idle containers exist. Returns number created."""
        created = 0
        with self._lock:
            idle_count = sum(1 for c in self._containers.values() if c.state == ContainerState.IDLE)
            total = len(self._containers)
            needed = self._min_idle - idle_count
            while needed > 0 and total + created < self._max_total:
                cid = self._start_container()
                info = ContainerInfo(
                    container_id=cid,
                    container_name=f"aio-worker-{uuid.uuid4().hex[:8]}",
                    state=ContainerState.IDLE,
                )
                self._containers[cid] = info
                created += 1
                needed -= 1
                _dbg("prefetch", cid=cid[:12], idle_after=idle_count + created)
        return created

    def health_check(self) -> dict:
        """Check all containers, mark dead ones. Returns health summary."""
        with self._lock:
            alive, dead = 0, 0
            for cid, info in list(self._containers.items()):
                if self._is_running(cid):
                    alive += 1
                else:
                    info.state = ContainerState.DEAD
                    dead += 1
                    _dbg("health_dead", cid=cid[:12])
            summary = {
                "total": len(self._containers),
                "alive": alive,
                "dead": dead,
                "idle": sum(1 for c in self._containers.values() if c.state == ContainerState.IDLE),
                "busy": sum(1 for c in self._containers.values() if c.state == ContainerState.BUSY),
                "dirty": sum(1 for c in self._containers.values() if c.state == ContainerState.DIRTY),
            }
            return summary

    def remove_dead(self) -> int:
        """Remove dead containers from tracking. Returns count removed."""
        removed = 0
        with self._lock:
            for cid in list(self._containers.keys()):
                if self._containers[cid].state == ContainerState.DEAD:
                    try:
                        self._rm_container(cid)
                    except Exception:
                        pass
                    del self._containers[cid]
                    removed += 1
        return removed

    def get_container_info(self, container_id: str) -> ContainerInfo | None:
        return self._containers.get(container_id)

    @property
    def total_containers(self) -> int:
        return len(self._containers)

    # ---- internal Docker operations ----

    def _start_container(self) -> str:
        name = f"aio-worker-{uuid.uuid4().hex[:8]}"
        args = [
            "docker", "run", "-d",
            "--name", name,
            "--label", "wisepen.role=aio-worker",
            "--security-opt", "seccomp=unconfined",
            "--shm-size", "2gb",
            "-v", f"{self._workspace_cache}:{self._workspace_cache}",
            self._image,
        ]
        _dbg("docker_run", name=name, image=self._image)
        raw = self._run_docker(args)
        cid = raw.strip()
        if not cid:
            raise RuntimeError("docker run returned empty container id")
        # Wait briefly for container to be ready
        time.sleep(1)
        return cid

    def _rm_container(self, container_id: str) -> None:
        try:
            self._run_docker(["rm", "-f", container_id])
        except Exception:
            pass

    def _is_running(self, container_id: str) -> bool:
        try:
            raw = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
                capture_output=True, text=True, timeout=5,
            )
            return raw.stdout.strip().lower() == "true"
        except Exception:
            return False

    def _run_docker(self, args: list[str]) -> str:
        completed = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=60,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"docker {' '.join(args[:2])} failed: {detail[:500]}"
            )
        return (completed.stdout or "").strip()
