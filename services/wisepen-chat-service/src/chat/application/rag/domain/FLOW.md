# RAG Domain Flow

## 资源与索引构建

```text
resource_lifecycle.py（资源生命周期）
  -> index_publication.py（索引发布）
  -> index_chunks.py（索引切块）
```

```mermaid
flowchart LR
    resource_lifecycle["resource_lifecycle.py<br/>资源生命周期"]
    index_publication["index_publication.py<br/>索引发布"]
    index_chunks["index_chunks.py<br/>索引切块"]

    resource_lifecycle --> index_publication --> index_chunks
```

## 检索与证据生成

```text
retrieval_planning.py（检索规划）
  -> retrieval_hits.py（多路召回命中）
  -> candidate_fusion.py（候选融合）
  -> evidence_hydration.py（证据水合）
  -> reranking.py（重排）
  -> parent_aggregation.py（父块聚合）
  -> evidence_selection.py（证据选择）
  -> evidence_output.py（证据输出）
  -> retrieval_execution.py（检索执行结果）
  -> answerability.py（可回答性判断）
  -> context_assembly.py（上下文组装）
```

```mermaid
flowchart LR
    retrieval_planning["retrieval_planning.py<br/>检索规划"]
    retrieval_hits["retrieval_hits.py<br/>多路召回命中"]
    candidate_fusion["candidate_fusion.py<br/>候选融合"]
    evidence_hydration["evidence_hydration.py<br/>证据水合"]
    reranking["reranking.py<br/>重排"]
    parent_aggregation["parent_aggregation.py<br/>父块聚合"]
    evidence_selection["evidence_selection.py<br/>证据选择"]
    evidence_output["evidence_output.py<br/>证据输出"]
    retrieval_execution["retrieval_execution.py<br/>检索执行结果"]
    answerability["answerability.py<br/>可回答性判断"]
    context_assembly["context_assembly.py<br/>上下文组装"]

    retrieval_planning --> retrieval_hits --> candidate_fusion --> evidence_hydration --> reranking
    reranking --> parent_aggregation --> evidence_selection --> evidence_output --> retrieval_execution
    retrieval_execution --> answerability --> context_assembly
```

## 内部契约与枚举

```text
enums.py（内部枚举）
  -> ports.py（领域端口）
```

```mermaid
flowchart LR
    enums["enums.py<br/>内部枚举"]
    ports["ports.py<br/>领域端口"]

    enums --> ports
```
