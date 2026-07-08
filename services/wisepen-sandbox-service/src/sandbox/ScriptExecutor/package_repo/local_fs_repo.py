from __future__ import annotations

import os
import shutil
import uuid

from sandbox.ScriptExecutor.scriptReader import ScriptFile, ScriptPackage, ScriptPackageRepository
from sandbox.core.errors import SandboxError, SandboxErrorCode
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][package_repo]")


class LocalFsScriptPackageRepository(ScriptPackageRepository):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def get(self, package_id: str) -> ScriptPackage:
        package_dir = os.path.join(self._base_dir, package_id)
        _dbg("get_begin", base_dir=self._base_dir, package_id=package_id, package_dir=package_dir)
        if not os.path.isdir(package_dir):
            _dbg("get_not_found", package_id=package_id)
            raise SandboxError(
                code=SandboxErrorCode.PACKAGE_NOT_FOUND,
                message="script package not found",
                detail=package_id,
            )
        files: list[ScriptFile] = []
        for root, _, filenames in os.walk(package_dir):
            for name in filenames:
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, package_dir).replace("\\", "/")
                with open(abs_path, "rb") as f:
                    files.append(ScriptFile(file_name=rel_path, content=f.read()))
        _dbg("get_end", package_id=package_id, file_count=len(files))
        return ScriptPackage(files=files, package_id=package_id, root_dir=".")

    def put(self, package: ScriptPackage) -> str:
        os.makedirs(self._base_dir, exist_ok=True)
        package_id = package.package_id or f"pkg_{uuid.uuid4().hex}"
        package_dir = os.path.join(self._base_dir, package_id)
        _dbg("put_begin", base_dir=self._base_dir, package_id=package_id, file_count=len(package.files))
        if os.path.exists(package_dir):
            shutil.rmtree(package_dir, ignore_errors=True)
        os.makedirs(package_dir, exist_ok=True)

        for f in package.files:
            rel = f.file_name.replace("\\", "/")
            if rel.startswith("/") or ".." in rel.split("/"):
                raise SandboxError(
                    code=SandboxErrorCode.VALIDATION_FAILED,
                    message="invalid file path in script package",
                    detail=rel,
                )
            abs_path = os.path.join(package_dir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as fp:
                fp.write(f.content)
        _dbg("put_end", package_id=package_id, package_dir=package_dir)
        return package_id
