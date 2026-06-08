from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

from common.clients.file_storage import FileStorageClient
from common.logger import log_error, log_event, log_fail

from chat.domain.interfaces.skill_asset_loader import SkillAssetLoader

# OSS 路径布局与发布侧保持一致
_OBJECT_KEY_PREFIX = "skills"


class OssSkillAssetLoader(SkillAssetLoader):
    """
    经 wisepen-file-storage-service 颁发的预签名 URL 从 OSS 拉取 Skill 资产
    把资产正文落在进程本地磁盘缓存里，一段时间未使用即被 GC 清理
    """

    def __init__(
        self,
        file_storage_client: FileStorageClient,
        *,
        cache_dir: Path,
        download_duration_seconds: int = 900,
        http_timeout: float = 10.0,
        cache_ttl_seconds: int = 6 * 3600,
        gc_interval_seconds: int = 30 * 60,
    ) -> None:
        self._fsc = file_storage_client
        self._duration = int(download_duration_seconds)
        self._cache_dir = Path(cache_dir).resolve()
        self._cache_ttl = float(cache_ttl_seconds)
        self._gc_interval = float(gc_interval_seconds)

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(http_timeout))
        self._fetch_locks: Dict[str, asyncio.Lock] = {}
        self._gc_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._gc_task is None or self._gc_task.done():
            self._gc_task = asyncio.create_task(self._gc_loop(), name="skill-asset-cache-gc")
            log_event(
                "Skill 资产磁盘缓存 GC 启动",
                cache_dir=str(self._cache_dir),
                ttl_seconds=int(self._cache_ttl),
                interval_seconds=int(self._gc_interval),
            )

    async def stop(self) -> None:
        if self._gc_task is not None:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except (asyncio.CancelledError, Exception):
                pass
            self._gc_task = None
        await self._http.aclose()


    async def load_by_object_key(self, object_key: str) -> bytes:
        if not object_key:
            raise ValueError("object_key 不能为空")

        # 计算缓存文件路径 (SHA1，避免 object_key 里有 / 导致目录嵌套)
        cache_path = self._cache_path(object_key)

        # 尝试读取缓存文件
        hit = self._read_if_fresh(cache_path)
        if hit is not None:
            return hit

        # 加锁，并发同 key 只下载一次
        lock = self._fetch_locks.setdefault(object_key, asyncio.Lock())
        async with lock:
            # 双检，防止在等待锁的时候别的协程下载好了缓存
            hit = self._read_if_fresh(cache_path)
            if hit is not None:
                return hit
            # 下载并写缓存
            content = await self._download(object_key)
            self._atomic_write(cache_path, content)
            return content

    async def load_asset(self, skill_id: str, version: str, path: str) -> bytes:
        return await self.load_by_object_key(self._derive_object_key(skill_id, version, path))

    # ---------- 内部实现 ----------

    def _read_if_fresh(self, cache_path: Path) -> Optional[bytes]:
        if not cache_path.is_file():
            return None
        try:
            # 每次读取缓存时，更新文件的修改时间 mtime
            os.utime(cache_path, None)
        except OSError:
            pass
        try:
            # 按字节读，资产可能是 .py/.md 文本，也可能是 .png/.pdf/.wasm 等二进制
            return cache_path.read_bytes()
        except OSError as e:
            log_fail("Skill 资产缓存读取", e, cache_path=str(cache_path))
            return None

    def _atomic_write(self, cache_path: Path, content: bytes) -> None:
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        try:
            tmp.write_bytes(content)
            os.replace(tmp, cache_path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    async def _download(self, object_key: str) -> bytes:
        # 先通过 FileStorageClient 申请一个预签名 URL，然后用 URL 下载
        url = await self._fsc.get_download_url(
            object_key=object_key,
            duration_seconds=self._duration,
        )
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log_error("Skill 资产 OSS 下载", e, object_key=object_key)
            raise
        content = resp.content
        log_event("Skill 资产写入磁盘缓存", object_key=object_key, bytes=len(content))
        return content

    def _cache_path(self, object_key: str) -> Path:
        # 文件名用 object_key 的 sha1
        digest = hashlib.sha1(object_key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.asset"

    async def _gc_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._gc_interval)
                self._gc_once()
        except asyncio.CancelledError:
            raise

    def _gc_once(self) -> None:
        cutoff = time.time() - self._cache_ttl
        removed = 0
        if not self._cache_dir.is_dir():
            return
        for p in self._cache_dir.iterdir():
            if not p.is_file():
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError as e:
                log_fail("Skill 资产缓存 GC 单文件", e, path=str(p))
        if removed:
            log_event("Skill 资产缓存 GC", removed=removed, ttl_seconds=int(self._cache_ttl))

    @staticmethod
    def _derive_object_key(skill_id: str, version: str, path: str) -> str:
        for seg, name in ((skill_id, "skill_id"), (version, "version")):
            if not seg or "/" in seg or "\\" in seg or seg in (".", ".."):
                raise ValueError(f"非法 {name}: {seg!r}")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"非法 path: {path!r}")
        return f"{_OBJECT_KEY_PREFIX}/{skill_id}/{version}/{path}"
