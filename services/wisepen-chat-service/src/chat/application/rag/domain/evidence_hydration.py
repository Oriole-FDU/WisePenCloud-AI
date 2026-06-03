from dataclasses import dataclass
from typing import List

from .index_chunks import RetrieveChunk
from .parent_aggregation import ParentCandidate
from .ports import RagRetrieveChunkLookup, RagSearchChunkLookup
from .ports import RagSearchChunkRecord


@dataclass(frozen=True, slots=True)
class SearchAndParentLookups:
    """候选证据水合所需的查询参数集合。

    包含从 search_chunk 表中查询完整记录（含检索上下文文本）所需的 lookups，
    以及从 retrieve_chunk 表中查询父块和邻居块所需的 lookups。
    """

    search_lookups: List[RagSearchChunkLookup]
    parent_lookups: List[RagRetrieveChunkLookup]


@dataclass(frozen=True, slots=True)
class RagHydratedCandidate:
    """水合后的完整候选证据。

    将原始检索命中的候选（仅有元数据）水合为包含完整文本、
    父块信息、邻居块信息和重排所需文本的完整结构。
    """

    candidate_id: str
    parent_candidate: ParentCandidate
    search_chunk_record: RagSearchChunkRecord
    parent_chunk: RetrieveChunk
    neighbor_chunks: List[RetrieveChunk]
    rerank_text: str
    mmr_text: str
    group_key: str
