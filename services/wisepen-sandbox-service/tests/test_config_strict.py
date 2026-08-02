from __future__ import annotations

import importlib
import os
import sys

import pytest
import yaml
from pydantic import ValidationError

os.environ.setdefault("NACOS_SERVER_ADDR", "127.0.0.1:8848")

from sandbox.core.config import nacos as nacos_module
from sandbox.container import Container, _load_provider
from sandbox.core.providers.aio_adapter.models import AdapterConfig
from sandbox.core.providers.aio_adapter.provider import AioSandboxProvider
from sandbox.core.storage.local import LocalWorkspaceStore


def complete_config() -> dict[str, object]:
    return {
        "FROM_SOURCE_SECRET": "internal-secret",
        "SANDBOX_PROVIDER_FACTORY": "sandbox.core.providers.aio_adapter.provider:AioSandboxProvider",
        "SANDBOX_WORKSPACE_ROOT": "/tmp/wisepen-workspaces",
        "SANDBOX_WORKSPACE_CACHE_MAX_FILES": 2000,
        "SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES": 2 * 1024 * 1024,
        "SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES": 64 * 1024 * 1024,
        "SANDBOX_WORKSPACE_CACHE_MANIFEST_NAME": ".wisepen-workspace-manifest.json",
        "SANDBOX_WORKSPACE_STORE_BACKEND": "local",
        "SANDBOX_MONGO_URL": "mongodb://mongo:27017",
        "SANDBOX_MONGO_DATABASE": "wisepen_sandbox",
        "SANDBOX_DOCKER_BIN": "docker",
        "SANDBOX_DOCKER_HOST": "127.0.0.1",
        "SANDBOX_DOCKER_NETWORK": "wisepen-network",
        "SANDBOX_AIO_PORT": 8080,
        "SANDBOX_VNC_PORT": 6080,
        "SANDBOX_REQUEST_TIMEOUT_SECONDS": 30.0,
        "SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS": 30.0,
        "SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS": 3.0,
        "SANDBOX_AIO_HEALTH_RETRY_INTERVAL_SECONDS": 0.5,
        "SANDBOX_DOCKER_CREATE_MAX_ATTEMPTS": 3,
        "SANDBOX_DOCKER_CREATE_RETRY_BACKOFF_SECONDS": 0.2,
        "SANDBOX_AIO_WORKDIR": "/home/gem",
        "SANDBOX_CONTAINER_WORKSPACE_ROOT": "/home/gem/workspaces",
        "SANDBOX_CONTAINER_USER": "gem:gem",
        "SANDBOX_DOCKER_TTY": True,
        "SANDBOX_OWNER_ID": "wisepen-sandbox-service",
        "SANDBOX_BROWSER_NO_SANDBOX": "",
        "SANDBOX_PUBLIC_VNC_URL_TEMPLATE": "https://sandbox.example/vnc/{container_name}",
        "SANDBOX_PUBLIC_WEBSOCKET_URL_TEMPLATE": "wss://sandbox.example/ws/{port}",
        "SANDBOX_CHECKPOINT_INTERVAL_SECONDS": 300.0,
        "SANDBOX_VNC_IDLE_TIMEOUT_SECONDS": 1800.0,
        "SANDBOX_VNC_IDLE_CLEANUP_INTERVAL_SECONDS": 300.0,
        "SANDBOX_IMAGE": "sandbox-worker:latest",
        "SANDBOX_LEASE_TTL_SECONDS": 1800,
        "SANDBOX_TARGET_READY": 2,
        "SANDBOX_MIN_READY": 1,
        "SANDBOX_READY_RESERVE": 0,
        "SANDBOX_MAX_CREATE_BATCH": 2,
        "SANDBOX_WARMUP_TIMEOUT_SECONDS": 60.0,
        "SANDBOX_DESTROY_TIMEOUT_SECONDS": 60.0,
        "SANDBOX_WARMUP_MAX_RETRIES": 3,
        "SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS": 5.0,
        "SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS": 60.0,
        "SANDBOX_WATCHER_INTERVAL_SECONDS": 5.0,
        "SANDBOX_LEADER_LEASE_TTL_SECONDS": 90.0,
        "SANDBOX_LEADER_LEASE_RENEW_INTERVAL_SECONDS": 20.0,
        "SANDBOX_DESTROY_MAX_RETRIES": 3,
        "SANDBOX_DESTROY_RETRY_BACKOFF_SECONDS": 0.1,
    }


@pytest.fixture
def app_settings_module(monkeypatch):
    async def pull_config():
        return yaml.safe_dump(complete_config())

    monkeypatch.setattr(nacos_module.nacos_client_manager, "pull_config", pull_config)
    sys.modules.pop("sandbox.core.config.app_settings", None)
    return importlib.import_module("sandbox.core.config.app_settings")


def test_nacos_pull_failure_is_fatal(app_settings_module, monkeypatch):
    async def pull_config():
        raise RuntimeError("nacos unavailable")

    monkeypatch.setattr(nacos_module.nacos_client_manager, "pull_config", pull_config)
    with pytest.raises(RuntimeError, match="nacos unavailable"):
        app_settings_module.load_settings()


@pytest.mark.parametrize("payload", ["", "[]", "null", "{}"])
def test_empty_or_non_mapping_nacos_config_is_fatal(
    app_settings_module, monkeypatch, payload
):
    async def pull_config():
        return payload

    monkeypatch.setattr(nacos_module.nacos_client_manager, "pull_config", pull_config)
    with pytest.raises(RuntimeError):
        app_settings_module.load_settings()


def test_missing_nacos_fields_do_not_use_defaults(app_settings_module, monkeypatch):
    config = complete_config()
    config.pop("SANDBOX_IMAGE")

    async def pull_config():
        return yaml.safe_dump(config)

    monkeypatch.setattr(nacos_module.nacos_client_manager, "pull_config", pull_config)
    with pytest.raises(ValidationError):
        app_settings_module.load_settings()


def test_browser_no_sandbox_defaults_to_empty_for_production(app_settings_module):
    config = complete_config()
    config.pop("SANDBOX_BROWSER_NO_SANDBOX")

    assert app_settings_module.AppSettings(**config).SANDBOX_BROWSER_NO_SANDBOX == ""


@pytest.mark.asyncio
async def test_nacos_registration_failure_is_fatal(monkeypatch):
    class Client:
        async def register_instance(self, request):
            raise RuntimeError("registration rejected")

    async def get_naming_client():
        return Client()

    manager = nacos_module.nacos_client_manager
    monkeypatch.setattr(manager, "get_naming_client", get_naming_client)
    monkeypatch.setattr(manager, "_resolve_host", lambda: "127.0.0.1")
    with pytest.raises(RuntimeError, match="registration rejected"):
        await manager.register_instance()


def test_container_builds_provider_transfer_and_selected_store_from_settings():
    container = Container()
    container.config.from_dict(complete_config())

    assert isinstance(container.provider(), AioSandboxProvider)
    assert isinstance(container.workspace_store(), LocalWorkspaceStore)
    assert container.provider()._file_transfer is container.file_transfer()
    assert container.provider()._workspace_root == "/home/gem/workspaces"
    assert container.watcher()._spec.environment == {}


def test_container_applies_nacos_runtime_parameters_to_services():
    config = complete_config()
    config.update(
        {
            "SANDBOX_LEASE_TTL_SECONDS": 1800,
            "SANDBOX_TARGET_READY": 10,
            "SANDBOX_MIN_READY": 5,
            "SANDBOX_READY_RESERVE": 0,
            "SANDBOX_MAX_CREATE_BATCH": 4,
            "SANDBOX_WARMUP_TIMEOUT_SECONDS": 180.0,
            "SANDBOX_DESTROY_TIMEOUT_SECONDS": 180.0,
            "SANDBOX_WARMUP_MAX_RETRIES": 5,
            "SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS": 2.0,
            "SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS": 20.0,
            "SANDBOX_WATCHER_INTERVAL_SECONDS": 3.0,
            "SANDBOX_LEADER_LEASE_TTL_SECONDS": 120.0,
            "SANDBOX_LEADER_LEASE_RENEW_INTERVAL_SECONDS": 30.0,
            "SANDBOX_DESTROY_MAX_RETRIES": 4,
            "SANDBOX_DESTROY_RETRY_BACKOFF_SECONDS": 0.3,
            "SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS": 4.0,
            "SANDBOX_AIO_HEALTH_RETRY_INTERVAL_SECONDS": 0.7,
            "SANDBOX_DOCKER_CREATE_MAX_ATTEMPTS": 4,
            "SANDBOX_DOCKER_CREATE_RETRY_BACKOFF_SECONDS": 0.3,
            "SANDBOX_CHECKPOINT_INTERVAL_SECONDS": 300.0,
            "SANDBOX_BROWSER_NO_SANDBOX": "--no-sandbox",
        }
    )
    container = Container()

    container.config.from_dict(config)
    pool = container.pool()
    watcher = container.watcher()
    scheduler = container.scheduler()

    assert (pool._lease_ttl, pool._target_ready, pool._min_ready) == (1800, 10, 5)
    assert (
        watcher._target_ready,
        watcher._min_ready,
        watcher._reserve,
        watcher._max_create_batch,
        watcher._warmup_timeout,
        watcher._destroy_timeout,
        watcher._interval,
        watcher._warmup_max_retries,
        watcher._warmup_retry_backoff,
        watcher._warmup_retry_max_backoff,
        watcher._leader_lease_ttl,
        watcher._leader_lease_renew_interval,
        watcher._checkpoint_interval,
    ) == (10, 5, 0, 4, 180.0, 180.0, 3.0, 5, 2.0, 20.0, 120.0, 30.0, 300.0)
    assert (scheduler._destroy_timeout, scheduler._destroy_max_retries, scheduler._destroy_backoff) == (
        180.0,
        4,
        0.3,
    )
    assert container.provider()._health_timeout == 4.0
    assert container.provider()._health_retry_interval == 0.7
    assert container.provider()._runtime._config.create_max_attempts == 4
    assert container.provider()._runtime._config.create_retry_backoff_seconds == 0.3
    assert watcher._spec.environment == {"BROWSER_NO_SANDBOX": "--no-sandbox"}
    assert container.provider()._runtime._config.browser_no_sandbox == "--no-sandbox"


def test_load_provider_passes_adapter_config_and_file_transfer(monkeypatch):
    settings = AdapterConfig(image="test-image")
    file_transfer = object()

    class Factory:
        @classmethod
        def from_settings(cls, received_settings, received_file_transfer):
            return received_settings, received_file_transfer

    monkeypatch.setattr(
        "sandbox.container.importlib.import_module", lambda _: type("Module", (), {"Factory": Factory})
    )

    assert _load_provider("test.module:Factory", file_transfer, settings) == (
        settings,
        file_transfer,
    )


@pytest.mark.parametrize("target", ["", "test.module", ":Factory", "test.module:"])
def test_load_provider_rejects_invalid_factory_target(target):
    with pytest.raises(RuntimeError, match="SANDBOX_PROVIDER_FACTORY"):
        _load_provider(target, object(), AdapterConfig())


def test_load_provider_requires_from_settings(monkeypatch):
    monkeypatch.setattr(
        "sandbox.container.importlib.import_module",
        lambda _: type("Module", (), {"Factory": object}),
    )

    with pytest.raises(RuntimeError, match="from_settings"):
        _load_provider("test.module:Factory", object(), AdapterConfig())
