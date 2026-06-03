# Web Search Domain Flow

```text
query_planning.py（查询规划）
  -> provider_routing.py（渠道路由）
  -> variant_execution.py（变体执行结果）
  -> result_ranking.py（结果排序）
```

```mermaid
flowchart LR
    query_planning["query_planning.py<br/>查询规划"]
    provider_routing["provider_routing.py<br/>渠道路由"]
    variant_execution["variant_execution.py<br/>变体执行结果"]
    result_ranking["result_ranking.py<br/>结果排序"]

    query_planning --> provider_routing --> variant_execution --> result_ranking
```
