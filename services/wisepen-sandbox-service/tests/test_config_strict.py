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
        "SANDBOX_AIO_WORKDIR": "/home/gem",
        "SANDBOX_CONTAINER_WORKSPACE_ROOT": "/home/gem/workspaces",
        "SANDBOX_CONTAINER_USER": "gem:gem",
        "SANDBOX_DOCKER_TTY": True,
        "SANDBOX_OWNER_ID": "wisepen-sandbox-service",
        "SANDBOX_PUBLIC_VNC_URL_TEMPLATE": "https://sandbox.example/vnc/{container_name}",
        "SANDBOX_PUBLIC_WEBSOCKET_URL_TEMPLATE": "wss://sandbox.example/ws/{port}",
        "SANDBOX_CHECKPOINT_INTERVAL_SECONDS": 300.0,
        "SANDBOX_IMAGE": "sandbox-worker:latest",
        "SANDBOX_LEASE_TTL_SECONDS": 1800,
        "SANDBOX_TARGET_READY": 2,
        "SANDBOX_MIN_READY": 1,
        "SANDBOX_READY_RESERVE": 0,
        "SANDBOX_MAX_CREATE_BATCH": 2,
        "SANDBOX_WARMUP_TIMEOUT_SECONDS": 60.0,
        "SANDBOX_DESTROY_TIMEOUT_SECONDS": 60.0,
        "SANDBOX_WARMUP_MAX_RETRIES": 3,
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
