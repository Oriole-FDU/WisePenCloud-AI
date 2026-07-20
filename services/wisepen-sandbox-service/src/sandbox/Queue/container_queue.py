"""
Container queue — manages a pool of AIO sandbox containers.

States: idle → busy → dirty → (recycle) → idle
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from common.sandbox import SandboxException
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][queue]")


class ContainerState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    DIRTY = "dirty"
    DEAD = "dead"


# 合法状态转换表：防止非法跳转
_ALLOWED_TRANSITIONS = {
    ContainerState.IDLE: {ContainerState.BUSY},
    ContainerState.BUSY: {ContainerState.DIRTY},
    ContainerState.DIRTY: {ContainerState.IDLE, ContainerState.DEAD},
    ContainerState.DEAD: set(),
}

@dataclass
class ContainerInfo:
    container_id: str
    container_name: str
    state: ContainerState = ContainerState.IDLE
    user_id: str = ""
    session_id: str = ""
    allocated_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    fencing_token: int = 0             # 每次 acquire 递增
    lease_expires_at: float = 0.0      # UTC epoch，超时强制回收


class ContainerQueue:
    """Thread-safe pool of AIO containers with acquire/release/recycle lifecycle."""

    def __init__(
        self,
        image: str = "ghcr.io/agent-infra/sandbox:latest",
        min_idle: int = 2,
        max_total: int = 8,
        workspace_cache: str = "/workspaces",
        lease_ttl: float = 1800.0,     # 租约 TTL（秒），默认 30 分钟
    ):
        self._image = image
        self._min_idle = min_idle
        self._max_total = max_total
        self._workspace_cache = workspace_cache.replace("\\", "/")
        self._lease_ttl = lease_ttl
        self._containers: dict[str, ContainerInfo] = {}
        self._lock = threading.Lock()
        self._next_token = 0

    # ---- public API ----

    def acquire(self, user_id: str, session_id: str) -> tuple[str, int]:
        """获取空闲容器，返回 (container_id, fencing_token)。"""
        with self._lock:
            idle = [c for c in self._containers.values() if c.state == ContainerState.IDLE]
            if idle:
                info = idle[0]
                self._next_token += 1
                self._transition(info, ContainerState.BUSY)
                info.user_id = user_id
                info.session_id = session_id
                info.allocated_at = time.time()
                info.fencing_token = self._next_token
                info.lease_expires_at = time.time() + self._lease_ttl
                _dbg("acquired", cid=info.container_id[:12], token=info.fencing_token, user=user_id)
                self._audit("acquire", info.container_id, user_id, f"token={info.fencing_token}")
                return info.container_id, info.fencing_token

            if len(self._containers) < self._max_total:
                _dbg("acquire_no_idle_prefetch", total=len(self._containers))
                cid = self._start_container()
                self._next_token += 1
                token = self._next_token
                info = ContainerInfo(
                    container_id=cid,
                    container_name=f"aio-worker-{uuid.uuid4().hex[:8]}",
                    state=ContainerState.BUSY,
                    user_id=user_id,
                    session_id=session_id,
                    allocated_at=time.time(),
                    fencing_token=token,
                    lease_expires_at=time.time() + self._lease_ttl,
                )
                self._containers[cid] = info
                _dbg("acquired_new", cid=cid[:12], token=token, user=user_id)
                self._audit("acquire_new", cid, user_id, f"token={token}")
                return cid, token

        raise SandboxException.queue_no_idle(
            total=len(self._containers), max_total=self._max_total,
        )

    def release(self, container_id: str, fencing_token: int = 0) -> None:
        """释放容器（必须出示正确的 fencing_token）。"""
        with self._lock:
            info = self._containers.get(container_id)
            if not info:
                _dbg("release_unknown", cid=container_id[:12])
                return
            if fencing_token and info.fencing_token != fencing_token:
                raise SandboxException(
                    code=SandboxException.queue_no_idle().code,
                    message=f"stale fencing token: expected {info.fencing_token}, got {fencing_token}",
                )
            self._transition(info, ContainerState.DIRTY)
            old_user = info.user_id
            info.user_id = ""
            info.session_id = ""
            info.lease_expires_at = 0.0
            _dbg("released", cid=container_id[:12], token=fencing_token)
            self._audit("release", container_id, old_user, f"token={fencing_token}")

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
            info.allocated_at = time.time()

            # Update key since container_id changed
            del self._containers[container_id]
            self._containers[new_cid] = info
            _dbg("recycled", old_cid=container_id[:12], new_cid=new_cid[:12])
            return new_cid

    def ensure_idle_count(self) -> int:
        """Ensure at least min_idle containers exist. Returns number created."""
        created_containers = 0
        with self._lock:
            idle_count = sum(1 for c in self._containers.values() if c.state == ContainerState.IDLE)
            total_containers = len(self._containers)
            needed_containers = self._min_idle - idle_count
            while needed_containers> 0 and total_containers + created_containers < self._max_total:
                cid = self._start_container()
                info = ContainerInfo(
                    container_id=cid,
                    container_name=f"aio-worker-{uuid.uuid4().hex[:8]}",
                    state=ContainerState.IDLE,
                )
                self._containers[cid] = info
                created_containers += 1
                needed_containers -= 1
                _dbg("prefetch", cid=cid[:12], idle_after=idle_count + created_containers)
        return created_containers

    def health_check(self) -> dict:
        """Check all containers, mark dead ones. Returns health summary."""
        with self._lock:
            alive, dead = 0, 0
            for cid, info in list(self._containers.items()):
                if self._is_running(cid):
                    alive += 1
                else:
                    self._transition(info, ContainerState.DEAD)
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
        removed_containers = 0
        with self._lock:
            for cid in list(self._containers.keys()):
                if self._containers[cid].state == ContainerState.DEAD:
                    try:
                        self._rm_container(cid)
                    except SandboxException:
                        pass
                    del self._containers[cid]
                    removed_containers += 1
        return removed_containers

    def get_container_info(self, container_id: str) -> ContainerInfo | None:
        return self._containers.get(container_id)

    @property
    def total_containers(self) -> int:
        return len(self._containers)

    @staticmethod
    def _audit(action: str, cid: str = "", user_id: str = "", detail: str = ""):
        _dbg("audit", action=action, container_id=cid[:12] if cid else "",
             user=user_id, detail=detail)

    # ---- internal Docker operations ----

    def _start_container(self) -> str:
        name = f"aio-worker-{uuid.uuid4().hex[:8]}"
        args = [
            "run", "-d",
            "--name", name,
            "--label", "wisepen.role=aio-worker",
            "--label", "wisepen.manager=sandbox-manager",
            # 资源限制 (P0)
            "--memory", "512m",
            "--memory-swap", "512m",
            "--cpus", "1.0",
            "--pids-limit", "64",
            "--restart", "no",
            # 安全加固 (P0)
            "--security-opt", "seccomp=unconfined",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--shm-size", "2gb",
            "--network", "sandbox-net",
            "-v", f"{self._workspace_cache}:/workspace:rw",
            "-p", "::8080",
            "-p", "::6080",
            self._image,
        ]
        _dbg("docker_run", name=name, image=self._image)
        raw = self._run_docker(args)
        cid = raw.strip()
        if not cid:
            raise SandboxException.container_start_failed("empty container id")
        # Wait briefly for container to be ready
        time.sleep(5)
        return cid

    def _rm_container(self, container_id: str) -> None:
        try:
            self._run_docker(["rm", "-f", container_id])
        except SandboxException:
            pass

    def _transition(self, info: ContainerInfo, target: ContainerState) -> None:
        """验证状态转换合法性后执行。"""
        allowed = _ALLOWED_TRANSITIONS.get(info.state, set())
        if target not in allowed:
            raise SandboxException(
                code=SandboxException.docker_error("state").code,
                message=f"invalid transition: {info.state.value} -> {target.value}",
            )
        info.state = target

    def remove_container(self, container_id: str) -> None:
        self._rm_container(container_id)

    @staticmethod
    def _is_running(container_id: str) -> bool:
        try:
            raw = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
                capture_output=True, text=True, timeout=5,
            )
            return raw.stdout.strip().lower() == "true"
        except Exception:
            _dbg("docker_run", container_id=container_id)
            return False

    @staticmethod
    def _run_docker(args: list[str]) -> str:
        completed = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=60,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise SandboxException.docker_error(" ".join(args[:2]), detail[:500])
        return (completed.stdout or "").strip()

    @property
    def containers(self):
        return self._containers

    @property
    def lock(self):
        return self._lock

    @property
    def min_idle(self):
        return self._min_idle

    @property
    def max_total(self):
        return self._max_total
