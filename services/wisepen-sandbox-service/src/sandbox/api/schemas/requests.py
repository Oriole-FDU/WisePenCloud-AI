from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AllocateBody(BaseModel):
    # 请求标识用于幂等分配；tenant/workspace 会参与路径隔离，限制为安全标识。
    request_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ExecuteBody(BaseModel):
    # 防护 token 必须由 allocate 返回，防止旧租约或错误会话继续操作沙箱。
    request_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    fencing_token: int = Field(gt=0)
    operation: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict)


class ReleaseBody(BaseModel):
    fencing_token: int = Field(gt=0)
