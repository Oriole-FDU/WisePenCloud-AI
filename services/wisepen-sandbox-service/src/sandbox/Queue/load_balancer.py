"""
Multi-server load balancing for container pool distribution.

For production deployments where a single Docker host cannot handle all
user sessions, ContainerPoolManager instances run on multiple servers.
The LoadBalancer distributes acquire() calls across servers by capacity.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---- Data types ----

@dataclass(frozen=True)
class ServerInfo:
    """Static metadata for a worker server in the pool."""
    server_id: str
    host: str
    port: int
    max_containers: int
    labels: dict[str, str] = field(default_factory=dict)  # e.g. {"zone": "cn-east", "gpu": "false"}


@dataclass(frozen=True)
class ServerStatus:
    """Runtime status snapshot of a worker server."""
    server_id: str
    healthy: bool
    total_containers: int
    idle_containers: int
    busy_containers: int
    dirty_containers: int
    dead_containers: int
    allocated_users: int
    last_heartbeat: float


# ---- Strategy interface ----

class SelectionStrategy(ABC):
    """Algorithm for picking a server from the pool."""

    @abstractmethod
    def select(self, candidates: list[ServerStatus]) -> ServerStatus:
        """Pick one server from healthy candidates. Raises if no candidates."""
        ...


# ---- Core interface ----

class ServerRegistry(ABC):
    """Maintains the pool of available servers."""

    @abstractmethod
    def register(self, info: ServerInfo) -> None:
        """Add a server to the pool."""
        ...

    @abstractmethod
    def deregister(self, server_id: str) -> None:
        """Remove a server from the pool."""
        ...

    @abstractmethod
    def heartbeat(self, server_id: str, status: ServerStatus) -> None:
        """Update runtime status for a server."""
        ...

    @abstractmethod
    def list_servers(self) -> dict[str, ServerStatus]:
        """Return status of all registered servers."""
        ...

    @abstractmethod
    def healthy_servers(self) -> list[ServerStatus]:
        """Return only servers that are healthy + have idle capacity."""
        ...


class LoadBalancer(ABC):
    """
    Coordinates container allocation across multiple worker servers.

    A ContainerPoolManager runs on each server.  One designated instance
    (or a separate coordinator) runs the LoadBalancer to route acquire()
    calls to the least-loaded server.

    Usage (per-request flow):
        server = balancer.select_server()
        cid = rpc_acquire(server.host, server.port, user_id, session_id)
        ...  # execute operations on that container
        rpc_release(server.host, server.port, cid)
    """

    # ---- Server management ----

    @abstractmethod
    def register_server(self, info: ServerInfo) -> None:
        """Notify the balancer that a new worker server is available."""
        ...

    @abstractmethod
    def deregister_server(self, server_id: str) -> None:
        """Remove a server from the pool (graceful shutdown)."""
        ...

    @abstractmethod
    def heartbeat(self, server_id: str, status: ServerStatus) -> None:
        """Accept periodic status report from a server."""
        ...

    # ---- Selection ----

    @abstractmethod
    def select_server(self, user_id: str = "", session_id: str = "",
                      labels: Optional[dict[str, str]] = None) -> ServerInfo:
        """
        Pick the best server for a new container.

        Args:
            user_id / session_id: may be used for affinity routing.
            labels: optional filter (e.g. {"zone": "cn-east"}).

        Returns:
            The ServerInfo of the selected server.

        Raises:
            NoAvailableServer: when no healthy server has idle capacity.
        """
        ...

    # ---- Metrics ----

    @abstractmethod
    def cluster_status(self) -> dict:
        """
        Return aggregate metrics across all servers.

        Response shape:
        {
            "servers": 4,
            "healthy": 4,
            "total_containers": 32,
            "idle_containers": 8,
            "busy_containers": 20,
            "allocated_users": 15,
            "utilization_pct": 62.5,
        }
        """
        ...

    # ---- Affinity (optional) ----

    @abstractmethod
    def assign_affinity(self, user_id: str, server_id: str) -> None:
        """Pin a user to a specific server so their workspace cache stays local."""
        ...

    @abstractmethod
    def remove_affinity(self, user_id: str) -> None:
        """Release affinity pin."""
        ...

    @abstractmethod
    def get_affinity(self, user_id: str) -> Optional[str]:
        """Return the server_id this user is pinned to, or None."""
        ...


# ---- Exception ----

class NoAvailableServer(Exception):
    """Raised when no healthy server has idle capacity."""
    ...
