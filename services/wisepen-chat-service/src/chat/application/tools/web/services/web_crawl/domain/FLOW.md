# Web Crawl Domain Flow

```text
frontier_scheduling.py（抓取队列调度）
  -> fetch_execution.py（抓取执行）
  -> link_discovery.py（链接发现）
```

```mermaid
flowchart LR
    frontier_scheduling["frontier_scheduling.py<br/>抓取队列调度"]
    fetch_execution["fetch_execution.py<br/>抓取执行"]
    link_discovery["link_discovery.py<br/>链接发现"]

    frontier_scheduling --> fetch_execution --> link_discovery
```
