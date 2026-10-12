"""只读雇佣工具 API；可信网关身份决定 snapshot，请求正文不能声明权限。"""

from typing import Annotated

from common.security import SecurityContextHolder
from common.web.middleware.security_header import SecurityHeaderMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.application.acl.models import PermissionScope
from rag.application.graph import GraphEvidence, GraphHit
from rag.application.reading import DocumentOutline, SectionReadScope, SectionText
from rag.application.retrieval import ChunkHit
from rag.application.snapshot import ResourceSnapshot
from rag.retrievers.graph import GraphRetrievalMode
from rag.storage.graph import GraphDirection

from .tools import EmploymentTools


class ContractSearch(BaseModel):
    """年份闭区间和员工精确匹配只用于专用合同集合。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1)
    year_from: int | None = Field(default=None, ge=1, le=9999)
    year_to: int | None = Field(default=None, ge=1, le=9999)
    employee: str | None = Field(default=None, min_length=1)
    top_k: int = Field(default=10, gt=0)
    candidate_limit: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def ordered_budget_and_years(self):
        if self.candidate_limit < self.top_k:
            raise ValueError("candidate_limit must not be less than top_k")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must not exceed year_to")
        return self


class GraphSearch(BaseModel):
    """通用三模式图检索的雇佣工具输入，不承诺业务时间过滤。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1)
    mode: GraphRetrievalMode = GraphRetrievalMode.HYBRID
    top_k: int = Field(default=10, gt=0, le=60)


class EmploymentTraversal(BaseModel):
    """逻辑种子 ID 保持原值；固定 in_department，None 同时读取两侧。"""

    model_config = ConfigDict(extra="forbid")
    node_ids: list[str]
    direction: GraphDirection | None = GraphDirection.OUTGOING

    @model_validator(mode="after")
    def nonblank_ids(self):
        if any(not node_id.strip() for node_id in self.node_ids):
            raise ValueError("node_ids must not contain blank IDs")
        return self


class SectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_ids: list[str]
    scope: SectionReadScope = SectionReadScope.OWN


def request_snapshot(request: Request) -> ResourceSnapshot:
    if not request.app.state.ready:
        raise HTTPException(503, "extension is not ready")
    user_id = SecurityContextHolder.get_user_id()
    if not user_id:
        raise HTTPException(401, "trusted user identity required")
    return ResourceSnapshot(
        PermissionScope.from_group_roles(
            user_id,
            SecurityContextHolder.get_group_role_map(),
        )
    )


Snapshot = Annotated[ResourceSnapshot, Depends(request_snapshot)]


def build_api(tools: EmploymentTools, *, from_source_secret: str) -> FastAPI:
    """子应用不创建或关闭客户端，其可用状态由宿主生命周期同步。"""
    api = FastAPI(title="Employment contract tools")
    api.state.ready = False
    api.add_middleware(SecurityHeaderMiddleware, from_source_secret=from_source_secret)

    @api.post("/contracts/search")
    async def search(body: ContractSearch, snapshot: Snapshot) -> list[ChunkHit]:
        return await tools.search_contracts(snapshot=snapshot, **body.model_dump())

    @api.post("/graph/search")
    async def graph(body: GraphSearch, snapshot: Snapshot) -> list[GraphHit]:
        return await tools.search_graph(snapshot=snapshot, **body.model_dump())

    @api.post("/graph/traverse")
    async def traverse(
        body: EmploymentTraversal, snapshot: Snapshot
    ) -> list[GraphEvidence]:
        return await tools.follow(snapshot=snapshot, **body.model_dump())

    @api.get("/contracts/{resource_id}/outline")
    async def outline(resource_id: str, snapshot: Snapshot) -> DocumentOutline:
        result = await tools.reading.global_outline(resource_id, snapshot=snapshot)
        if result is None:
            raise HTTPException(404, "contract not visible")
        return result

    @api.post("/contracts/sections")
    async def sections(body: SectionRequest, snapshot: Snapshot) -> list[SectionText]:
        return await tools.reading.read_sections(snapshot=snapshot, **body.model_dump())

    return api
