from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from common.utils.ranking import ScoreSignal
from common.core.domain import GroupRoleType


@dataclass(frozen=True, slots=True)
class RagPermissionScope:
    """可信请求上下文提供的用户和群组身份。"""

    user_id: str  # 当前请求用户 ID，所有 ACL 判断都基于此身份。
    group_role_map: Mapping[str, GroupRoleType | None]  # 用户在每个群组中的角色，未加入时为 None。

    @property
    def managed_group_ids(self) -> tuple[str, ...]:
        return tuple(
            group_id
            for group_id, role in self.group_role_map.items()
            if role in (GroupRoleType.OWNER, GroupRoleType.ADMIN)
        )

    @property
    def joined_group_ids(self) -> tuple[str, ...]:
        return tuple(
            group_id
            for group_id, role in self.group_role_map.items()
            if role is not None and role is not GroupRoleType.NOT_MEMBER
        )


@dataclass(frozen=True, slots=True)
class RagCandidateRequest:
    """一次召回请求的完整入参。"""

    query_text: str  # 召回查询文本，用于 BM25 等文本召回信号。
    query_vector: Sequence[float]  # 与 query_text 对应的稠密向量，用于向量召回。
    permission_scope: RagPermissionScope  # 用于在召回阶段下发 ACL 过滤条件。
    resource_ids: tuple[str, ...] = ()  # 限定召回范围；空表示不限制。
    limit: int = 80  # 单次召回的最大候选数量（向量召回 top_k）。


@dataclass(frozen=True, slots=True)
class RagRetrievalCandidate:
    """召回阶段产出的单一 chunk 候选。"""

    chunk_id: str  # 内容投影内的全局唯一 chunk ID。
    reading_block_id: str  # 命中后用于回读正文的 Section ReadingBlock ID。
    section_id: str  # 唯一所属 Section ID。
    section_path: tuple[str, ...]  # 所属 Section 的标题路径。
    resource_id: str  # 该 chunk 所属的私有资源 ID。
    content_revision: str  # 内容投影的内容版本哈希，与 ACL/向量库对齐。
    raw_text: str  # chunk 原文，作为后续排序与证据回源基础。
    anchor_labels: tuple[str, ...]  # 文档锚点标签，用于人工定位。
    source_ref_id: str  # 该 chunk 的精确 SourceRef，决定最终证据回源。
    signals: tuple[ScoreSignal, ...]  # 召回阶段产出的原始打分信号，供排序层融合。


@dataclass(frozen=True, slots=True)
class RagRetrievalRequest:
    """完整 RAG 检索请求。"""

    query: str  # 原始查询文本，会同时用于 embedding 与精排。
    permission_scope: RagPermissionScope  # 检索上下文身份，决定 ACL 过滤与可见资源。
    resource_ids: tuple[str, ...] = ()  # 资源白名单；空表示不限制。
    top_k: int = 10  # 最终返回的命中数量。
    candidate_limit: int = 80  # 召回阶段的最大候选数量，决定后续精排规模。
