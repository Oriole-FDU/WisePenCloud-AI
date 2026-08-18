"""知识图谱抽取子包：把已发布的 ReadingBlock 转为可合并的领域候选。

主要能力：
- KnowledgeGraphExtractor：编排窗口构建、候选抽取、派生产物复用与确定性校验。
- QueryClientGraphRagLLM：把项目 QueryClient 适配为 GraphRAG SDK 的 LLM 接口。
- build_extraction_windows：把 ReadingBlock 切成可精确回源的抽取窗口。
"""

from .extractor import KnowledgeGraphExtractor
from .llm import QueryClientGraphRagLLM
from .windows import build_extraction_windows
from .candidate_merge import merge_candidate_graph

__all__ = [
    "KnowledgeGraphExtractor",
    "QueryClientGraphRagLLM",
    "build_extraction_windows",
    "merge_candidate_graph"
]
