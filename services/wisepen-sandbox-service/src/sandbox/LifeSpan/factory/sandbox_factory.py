from __future__ import annotations

from sandbox.LifeSpan.sandboxLifespan import SandboxFactory
from sandbox.LifeSpan.providers.docker_provider import DockerSandboxProviderImpl


class DefaultSandboxFactory(SandboxFactory):
    def __init__(self) -> None:
        super().__init__()
        self.register_provider("docker", DockerSandboxProviderImpl)
