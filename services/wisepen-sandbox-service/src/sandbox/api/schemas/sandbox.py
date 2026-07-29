from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sandbox.domain.entities import (
    Endpoint,
    ExecutionResult,
    SandboxLease,
    SandboxRecord,
    SandboxState,
)


class AllocateRequest(BaseModel):
    """分配一个预热沙箱并创建短期租约的请求。"""

    # 请求标识用于幂等分配；tenant/workspace 会参与路径隔离，限制为安全标识。
    request_id: str = Field(
        ..., min_length=1, max_length=200, description="分配请求 ID；相同 ID 重试时返回原租约。"
    )
    tenant_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="租户 ID；只允许字母、数字、下划线和连字符。",
    )
    workspace_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="工作区 ID；只允许字母、数字、下划线和连字符。",
    )


class ExecuteRequest(BaseModel):
    """通过租约向当前沙箱转发一次执行请求。"""

    # 防护 token 必须由 allocate 返回，防止旧租约或错误会话继续操作沙箱。
    request_id: str = Field(
        ..., min_length=1, max_length=200, description="本次执行请求 ID。"
    )
    tenant_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="租约所属租户 ID。",
    )
    workspace_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="租约所属工作区 ID。",
    )
    fencing_token: int = Field(
        ..., gt=0, description="由 allocate 返回的租约 fencing token。"
    )
    operation: str = Field(
        ..., min_length=1, max_length=50, description="要执行的沙箱操作名称。"
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="传递给沙箱操作的业务参数。"
    )


class ReleaseRequest(BaseModel):
    """关闭租约并触发工作区提交和沙箱销毁的请求。"""

    fencing_token: int = Field(
        ..., gt=0, description="由 allocate 返回的租约 fencing token。"
    )


class EndpointResponse(BaseModel):
    """分配给当前租约的沙箱访问端点。"""

    base_url: str = Field(..., description="沙箱内部访问地址。")
    token: str | None = Field(default=None, description="沙箱访问 token；可能为空。")

    @classmethod
    def from_entity(cls, endpoint: Endpoint) -> "EndpointResponse":
        return cls(base_url=endpoint.base_url, token=endpoint.token)


class SandboxLeaseResponse(BaseModel):
    """沙箱租约信息；endpoint token 仅在分配响应中返回。"""

    lease_id: str = Field(..., description="租约 ID。")
    request_id: str = Field(..., description="分配请求 ID。")
    sandbox_id: str = Field(..., description="沙箱实例 ID。")
    tenant_id: str = Field(..., description="租约所属租户 ID。")
    workspace_id: str = Field(..., description="租约所属工作区 ID。")
    expires_at: datetime = Field(..., description="租约过期时间。")
    fencing_token: int = Field(..., gt=0, description="用于拒绝旧租约请求的 fencing token。")
    endpoint: EndpointResponse | None = Field(default=None, description="沙箱访问端点。")

    @classmethod
    def from_entity(cls, lease: SandboxLease) -> "SandboxLeaseResponse":
        return cls(
            lease_id=lease.lease_id,
            request_id=lease.request_id,
            sandbox_id=lease.sandbox_id,
            tenant_id=lease.tenant_id,
            workspace_id=lease.workspace_id,
            expires_at=lease.expires_at,
            fencing_token=lease.fencing_token,
            endpoint=(EndpointResponse.from_entity(lease.endpoint) if lease.endpoint else None),
        )


class ExecutionResultResponse(BaseModel):
    """沙箱操作执行结果。"""

    request_id: str = Field(..., description="本次执行请求 ID。")
    status: str = Field(..., description="执行状态。")
    data: dict[str, Any] = Field(default_factory=dict, description="操作返回数据。")

    @classmethod
    def from_entity(cls, result: ExecutionResult) -> "ExecutionResultResponse":
        return cls(request_id=result.request_id, status=result.status, data=result.data)


class ReleaseResponse(BaseModel):
    """租约释放确认。"""

    status: Literal["released"] = Field(..., description="释放状态。")


class SandboxStatusEndpointResponse(BaseModel):
    """状态查询中的非敏感端点信息，不包含访问 token。"""

    base_url: str = Field(..., description="沙箱内部访问地址。")


class SandboxStatusRefResponse(BaseModel):
    """状态查询中的沙箱引用，不包含 Provider 内部标识和 metadata。"""

    sandbox_id: str = Field(..., description="沙箱实例 ID。")
    endpoint: SandboxStatusEndpointResponse | None = Field(
        default=None, description="非敏感沙箱访问地址。"
    )


class SandboxStatusResponse(BaseModel):
    """沙箱管理状态的安全投影。"""

    ref: SandboxStatusRefResponse = Field(..., description="沙箱引用。")
    state: SandboxState = Field(..., description="沙箱生命周期状态。")
    created_at: datetime = Field(..., description="实例创建时间。")
    updated_at: datetime = Field(..., description="状态更新时间。")
    lease_id: str | None = Field(default=None, description="当前租约 ID。")
    request_id: str | None = Field(default=None, description="当前分配请求 ID。")
    tenant_id: str | None = Field(default=None, description="当前租约租户 ID。")
    workspace_id: str | None = Field(default=None, description="当前租约工作区 ID。")
    lease_expires_at: datetime | None = Field(default=None, description="当前租约过期时间。")
    fencing_token: int = Field(..., ge=0, description="当前 fencing token。")
    state_version: int = Field(..., ge=0, description="状态版本号。")
    last_error: str | None = Field(default=None, description="最近一次生命周期错误。")

    @classmethod
    def from_entity(cls, record: SandboxRecord) -> "SandboxStatusResponse":
        endpoint = record.ref.endpoint
        return cls(
            ref=SandboxStatusRefResponse(
                sandbox_id=record.ref.sandbox_id,
                endpoint=(
                    SandboxStatusEndpointResponse(base_url=endpoint.base_url)
                    if endpoint
                    else None
                ),
            ),
            state=record.state,
            created_at=record.created_at,
            updated_at=record.updated_at,
            lease_id=record.lease_id,
            request_id=record.request_id,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            lease_expires_at=record.lease_expires_at,
            fencing_token=record.fencing_token,
            state_version=record.state_version,
            last_error=record.last_error,
        )
