from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from common.logger import error, info, warn

from sandbox.application.container_manager import ContainerManager


# 派生 Dockerfile 随 sandbox 包一起分发，按 provider_id 分目录存放
_DOCKERFILES_DIR = Path(__file__).resolve().parent.parent / "dockerfiles"

_LABEL_BASE_DIGEST = "com.wisepen.sandbox.base-digest"
_LABEL_DOCKERFILE_SHA = "com.wisepen.sandbox.dockerfile-sha"

# 构建失败后的重试间隔
# 避免持续失败的 build 拖住 watcher 维护循环
_BUILD_RETRY_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class ResolvedImage:
    """预热前镜像解析结果"""

    image: str
    derived: bool


class ImageBuilder:
    """在预热前确保派生镜像存在，失败时退回基础镜像
    基础镜像更新或 Dockerfile 变更时自动触发重建
    """

    def __init__(self, container_manager: ContainerManager) -> None:
        self._docker = container_manager
        self._last_build_attempt_monotonic: float | None = None

    async def ensure_image(
        self,
        base_image: str,
        provider_id: str,
        *,
        enabled: bool = True,
    ) -> ResolvedImage:
        """返回预热应使用的镜像名。构建失败时退回基础镜像，不抛异常。"""
        if not enabled:
            return ResolvedImage(image=base_image, derived=False)

        dockerfile_path = _DOCKERFILES_DIR / provider_id.lower() / "Dockerfile"
        try:
            dockerfile_bytes = dockerfile_path.read_bytes()
        except OSError as exc:
            error(
                "sandbox derived Dockerfile unreadable; fallback to base image",
                provider=provider_id,
                path=str(dockerfile_path),
                exc=exc,
            )
            return ResolvedImage(image=base_image, derived=False)

        try:
            # 获取基础镜像指纹
            # 这里不直接使用 tag 名，因为仓库理论上可以在不换 tag 的情况下重新 push 一个不同内容
            base_digest = await self._resolve_base_digest(base_image)
            derived_tag = self._compose_tag(base_digest, dockerfile_bytes, provider_id)
            if not await self._image_exists(derived_tag):
                info(
                    "sandbox derived image missing; building",
                    derived=derived_tag,
                    base=base_image,
                    provider=provider_id,
                )
                if not await self._build(base_image, derived_tag, base_digest, dockerfile_bytes, dockerfile_path):
                    return ResolvedImage(image=base_image, derived=False)
            return ResolvedImage(image=derived_tag, derived=True)
        except Exception as exc:
            error(
                "sandbox image ensure failed; fallback to base image",
                exc=exc,
                image=base_image,
                provider=provider_id,
            )
            return ResolvedImage(image=base_image, derived=False)

    async def _resolve_base_digest(self, base_image: str) -> str:
        # 读这个镜像的 RepoDigests 列表
        digest = await self._inspect_field(base_image, "{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}")
        if digest:
            return digest

        # 查不到说明本地还没有这个镜像，执行 `docker pull --quiet` 把它拉下来
        returncode, _, stderr = await self._docker.run_docker_command("pull", "--quiet", base_image)
        if returncode != 0:
            raise RuntimeError(f"docker pull failed: {stderr}")

        # 拉完再查一次 RepoDigests
        digest = await self._inspect_field(base_image, "{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}")
        if digest:
            return digest
        # 还没有，用 .Id 兜底
        digest = await self._inspect_field(base_image, "{{.Id}}")
        if digest:
            return digest
        # 仍然拿不到就报错
        raise RuntimeError(f"could not resolve image digest for {base_image}")

    async def _build(
        self,
        base_image: str,
        derived_tag: str,
        base_digest: str,
        dockerfile_bytes: bytes,
        dockerfile_path: Path,
    ) -> bool:
        if self._build_throttled():
            warn("sandbox derived image build retry throttled", derived=derived_tag)
            return False

        # 基础镜像若配置了非 root 的运行时用户，构建完成后恢复该用户
        # 保证派生镜像的运行时身份与基础镜像一致
        runtime_user = await self._inspect_field(base_image, "{{.Config.User}}")
        args = [
            "build",
            "-f", str(dockerfile_path),
            "-t", derived_tag,
            "--label", f"{_LABEL_BASE_DIGEST}={base_digest}",
            "--label", f"{_LABEL_DOCKERFILE_SHA}={hashlib.sha256(dockerfile_bytes).hexdigest()}",
            "--build-arg", f"BASE_IMAGE={base_image}",
            "--build-arg", f"SANDBOX_RUNTIME_USER={runtime_user or 'root'}",
            str(dockerfile_path.parent),
        ]
        returncode, _, stderr = await self._docker.run_docker_command(*args)
        if returncode != 0:
            error("docker build failed", derived=derived_tag, stderr=stderr)
            return False
        return True

    def _build_throttled(self) -> bool:
        now = time.monotonic()
        if (
            self._last_build_attempt_monotonic is not None
            and now - self._last_build_attempt_monotonic < _BUILD_RETRY_INTERVAL_SECONDS
        ):
            return True
        self._last_build_attempt_monotonic = now
        return False

    async def _image_exists(self, image: str) -> bool:
        returncode, _, _ = await self._docker.run_docker_command("image", "inspect", image)
        return returncode == 0

    async def _inspect_field(self, image: str, go_template: str) -> str:
        returncode, stdout, _ = await self._docker.run_docker_command(
            "image", "inspect", "--format", go_template, image,
        )
        return stdout.strip() if returncode == 0 else ""

    @staticmethod
    def _compose_tag(base_digest: str, dockerfile_bytes: bytes, provider_id: str) -> str:
        digest = hashlib.sha256()
        digest.update(base_digest.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(dockerfile_bytes)
        return f"wisepen-{provider_id.lower()}:{digest.hexdigest()[:16]}"
