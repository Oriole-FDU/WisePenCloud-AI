from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4


@dataclass(frozen=True)
class ServiceInstance:
    service_name: str
    instance_id: str
    host: str
    port: int
    metadata: Dict[str, Any] = field(default_factory=dict)


class NacosClient(ABC):
    @abstractmethod
    def register(self, instance: ServiceInstance) -> None:
        raise NotImplementedError

    @abstractmethod
    def deregister(self, instance: ServiceInstance) -> None:
        raise NotImplementedError

    @abstractmethod
    def discover(self, service_name: str) -> List[ServiceInstance]:
        raise NotImplementedError

    @abstractmethod
    def get_config(self, data_id: str, group: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def publish_config(self, data_id: str, group: str, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def watch_config(self, data_id: str, group: str, on_change: Callable[[str], None]) -> None:
        raise NotImplementedError


class InMemoryNacosClient(NacosClient):
    def __init__(self) -> None:
        self._lock = Lock()
        self._services: Dict[str, Dict[str, ServiceInstance]] = {}
        self._configs: Dict[Tuple[str, str], str] = {}
        self._watchers: Dict[Tuple[str, str], List[Callable[[str], None]]] = {}

    def register(self, instance: ServiceInstance) -> None:
        with self._lock:
            if not instance.instance_id:
                raise ValueError("instance_id must be non-empty")
            if instance.service_name not in self._services:
                self._services[instance.service_name] = {}
            self._services[instance.service_name][instance.instance_id] = instance

    def deregister(self, instance: ServiceInstance) -> None:
        with self._lock:
            if instance.service_name not in self._services:
                return
            self._services[instance.service_name].pop(instance.instance_id, None)

    def discover(self, service_name: str) -> List[ServiceInstance]:
        with self._lock:
            items = self._services.get(service_name, {})
            return list(items.values())

    def get_config(self, data_id: str, group: str) -> str:
        key = (data_id, group)
        with self._lock:
            if key not in self._configs:
                raise KeyError(f"Config '{data_id}' in group '{group}' not found.")
            return self._configs[key]

    def publish_config(self, data_id: str, group: str, content: str) -> None:
        key = (data_id, group)
        callbacks: List[Callable[[str], None]] = []
        with self._lock:
            self._configs[key] = content
            callbacks = list(self._watchers.get(key, []))
        for cb in callbacks:
            try:
                cb(content)
            except Exception:
                pass

    def watch_config(self, data_id: str, group: str, on_change: Callable[[str], None]) -> None:
        key = (data_id, group)
        with self._lock:
            if key not in self._watchers:
                self._watchers[key] = []
            self._watchers[key].append(on_change)


class NacosSdkClient(NacosClient):
    def __init__(
        self,
        server_addresses: str,
        *,
        namespace: str = "public",
        username: Optional[str] = None,
        password: Optional[str] = None,
        default_group: str = "DEFAULT_GROUP",
        config_timeout: float = 5.0,
        watch_interval_s: float = 2.0,
    ) -> None:
        self._server_addresses = server_addresses
        self._namespace = namespace
        self._username = username
        self._password = password
        self._default_group = default_group
        self._config_timeout = config_timeout
        self._watch_interval_s = watch_interval_s
        self._client = self._create_client()

    def register(self, instance: ServiceInstance) -> None:
        self._client.add_naming_instance(
            service_name=instance.service_name,
            ip=instance.host,
            port=instance.port,
            instance_id=instance.instance_id,
            metadata=instance.metadata or {},
        )

    def deregister(self, instance: ServiceInstance) -> None:
        self._client.remove_naming_instance(
            service_name=instance.service_name,
            ip=instance.host,
            port=instance.port,
            instance_id=instance.instance_id,
        )

    def discover(self, service_name: str) -> List[ServiceInstance]:
        raw = self._client.list_naming_instance(service_name=service_name, group_name=self._default_group)
        hosts = raw.get("hosts") if isinstance(raw, dict) else None
        if not isinstance(hosts, list):
            return []
        instances: List[ServiceInstance] = []
        for h in hosts:
            if not isinstance(h, dict):
                continue
            ip = h.get("ip")
            port = h.get("port")
            if not ip or port is None:
                continue
            instance_id = h.get("instanceId") or h.get("instance_id") or uuid4().hex
            metadata = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
            instances.append(
                ServiceInstance(
                    service_name=service_name,
                    instance_id=str(instance_id),
                    host=str(ip),
                    port=int(port),
                    metadata=metadata,
                )
            )
        return instances

    def get_config(self, data_id: str, group: str) -> str:
        content = self._client.get_config(data_id=data_id, group=group, timeout=self._config_timeout)
        if content is None:
            raise KeyError(f"Config '{data_id}' in group '{group}' not found.")
        return str(content)

    def publish_config(self, data_id: str, group: str, content: str) -> None:
        ok = self._client.publish_config(data_id=data_id, group=group, content=content)
        if ok is not True:
            raise RuntimeError("publish_config failed")

    def watch_config(self, data_id: str, group: str, on_change: Callable[[str], None]) -> None:
        stop = Event()

        def _loop() -> None:
            last: Optional[str] = None
            while not stop.is_set():
                try:
                    current = self.get_config(data_id, group)
                except Exception:
                    current = None
                if current is not None and current != last:
                    last = current
                    try:
                        on_change(current)
                    except Exception:
                        pass
                stop.wait(self._watch_interval_s)

        t = Thread(target=_loop, daemon=True, name=f"nacos-watch-{data_id}-{group}")
        t.start()

    def _create_client(self) -> Any:
        try:
            import nacos  # type: ignore
        except Exception as e:
            raise RuntimeError(f"nacos-sdk-python is required for NacosSdkClient: {e}")

        kwargs: Dict[str, Any] = {"namespace": self._namespace}
        if self._username is not None:
            kwargs["username"] = self._username
        if self._password is not None:
            kwargs["password"] = self._password
        return nacos.NacosClient(self._server_addresses, **kwargs)
