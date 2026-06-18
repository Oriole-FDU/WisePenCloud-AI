# 父子分块摄取指南（后续计划）

本文档面向后续「父子双仓储」改造，说明如何从 `chunking_engine` 的 `nested_markdown` pipeline 产出中摄取父子 chunk。当前阶段仅 chunking_engine 层面完成了父子区分与关系映射，存储层/检索层尚未拆分，本文档作为后续接手者的设计参考。

## 1. chunking_engine 产出的父子契约

`nested_markdown` pipeline 的执行顺序：

```text
MarkdownPreProcessor → MarkdownBlockSplitter → BlockAwarePacker
  → SecondaryChunkProcessor → SecondaryChunkFinalizer → ChunkExtraIndexer
```

其中 `SecondaryChunkProcessor` 在 `SecondaryChunkFinalizer` 之前执行：先用父 chunk 的临时 ID 生成子 chunk 并建立 `parent_chunk_id` 引用，再由 `SecondaryChunkFinalizer` 对父 chunk 做合并（heading-only + short-tails），通过 `remapped_ids` 更新子 chunk 的 `parent_chunk_id`，最后统一为父子 chunk 生成最终 ID 和 `content_hash`。

### 1.1 父子区分维度

| 维度 | 父 chunk | 子 chunk |
| --- | --- | --- |
| `level` | `ChunkLevel.RETRIEVAL` | `ChunkLevel.SEARCH` |
| `parent_chunk_id` | `None` | 指向父 chunk 的 `chunk_id` |
| `chunk_id` 格式 | `{prefix}:retrieval:{index}:{hash[:16]}` | `{prefix}:search:{index}:{hash[:16]}` |
| `content_hash` | SHA-256 of text | SHA-256 of text |
| `chunk_index` | 全局连续编号（与子 chunk 共享同一序号空间，不冲突） | 全局连续编号 |
| `metadata["child_index"]` | 无 | 子在父内的序号 |
| `metadata["child_count"]` | 无 | 父被拆出的子 chunk 总数 |
| `start_offset / end_offset` | 相对原文 | 相对原文（已换算：父 offset + 子在父文本内 offset） |

### 1.2 父子映射关系示例

```text
父 read:0:1a986231df20f161  (level=read,  parent=None)
  ├─ 子 retrieve:1:67dae6d6a9e48a53  (level=retrieve, parent=read:0:1a986231df20f161)
  ├─ 子 retrieve:2:b59e58442568b61b  (level=retrieve, parent=read:0:1a986231df20f161)
  └─ 子 retrieve:3:981b297cc77e6110  (level=retrieve, parent=read:0:1a986231df20f161)
```

### 1.3 摄取时应依赖的字段

- `chunk_id`：父子 chunk 的稳定唯一标识，子 chunk 的 `parent_chunk_id` 引用此字段。
- `level`：区分父子（`READ` vs `RETRIEVE`），是拆分到双仓储的 discriminator。
- `parent_chunk_id`：跨仓储外键，子仓储通过此字段关联回父仓储。
- `content_hash`：幂等去重依据，相同内容产生相同 hash。
- `start_offset / end_offset`：在原文中的字符偏移，父仓储可用此字段从 `StoredToolContent.text` 切片还原父 chunk 文本，无需单独存储全文。
- `chunk_index`：全局顺序，用于回退场景下的连续读取。
- `metadata["child_index"] / metadata["child_count"]`：子 chunk 在父内的位置信息，可用于 AutoMergingRetriever 的合并阈值判断。

## 2. 父子双仓储职责划分

| 仓储 | 存储内容 | 用途 | 检索方式 |
| --- | --- | --- | --- |
| 父仓储（context store） | 父 chunk 元数据（offset/hash/index） | RAG 上下文注入 | 按 `chunk_id` 精确取回 |
| 子仓储（retrieval store） | 子 chunk 文本 + embedding + `parent_chunk_id` | 精准向量检索 | 向量相似度 + top-k |

父仓储不存全文，只存 offset，从 `StoredToolContent.text` 切片还原——与现有 `ToolContentChunk` 的设计一致。子仓储需要存子 chunk 文本（因为子 chunk 文本不等于父 chunk 文本的连续切片，RecursiveTextSplitter 可能产生 overlap）。

## 3. 摄取流程

```text
ChunkingResult.chunks
  │
  ├─ filter(level == READ)    → 父 chunk 列表  → 父仓储.put(parent_chunks)
  └─ filter(level == RETRIEVE) → 子 chunk 列表  → 子仓储.put(child_chunks, embeddings)
```

### 3.1 伪代码

```python
def ingest(result: ChunkingResult, *, content_id: str) -> None:
    parents = [c for c in result.chunks if c.level == ChunkLevel.RETRIEVAL]
    children = [c for c in result.chunks if c.level == ChunkLevel.SEARCH]

    # 1. 父 chunk 写入父仓储（只存元数据，文本通过 offset 从 StoredToolContent 切片）
    parent_store.put(
        content_id=content_id,
        chunks=[
            ParentChunkRecord(
                chunk_id=p.chunk_id,
                chunk_index=p.chunk_index,
                start_offset=p.start_offset,
                end_offset=p.end_offset,
                content_hash=p.content_hash,
                section_path=p.metadata.get("section_paths", ()),
            )
            for p in parents
        ],
    )

    # 2. 子 chunk 写入子仓储（存文本 + embedding + parent_chunk_id 外键）
    child_records = []
    for c in children:
        embedding = embed_model.encode(c.text)
        child_records.append(
            ChildChunkRecord(
                chunk_id=c.chunk_id,
                parent_chunk_id=c.parent_chunk_id,  # 跨仓储外键
                text=c.text,
                embedding=embedding,
                content_hash=c.content_hash,
                child_index=c.metadata.get("child_index"),
                child_count=c.metadata.get("child_count"),
            )
        )
    child_store.put(child_records)
```

### 3.2 事务一致性

父子写入应保证原子性：

- 父仓储写入成功后，子仓储写入失败 → 需回滚父仓储或记录孤儿子 chunk 待清理。
- 推荐顺序：先写父仓储，再写子仓储。子 chunk 的 `parent_chunk_id` 引用完整性可由父仓储写入成功保证。
- 若子仓储写入失败，父仓储的 chunk 仍可作为普通单层 chunk 降级使用（父 chunk 文本完整，只是失去精准检索能力）。

## 4. 检索时父子联动

参考业界最佳实践：

### 4.1 LangChain `ParentDocumentRetriever` 模式

1. 子 chunk 写入 vectorstore（带 `parent_chunk_id` metadata）
2. 父 chunk 写入 docstore（key = parent chunk_id）
3. 检索：vectorstore 召回子 chunk → 取 `parent_chunk_id` → docstore 取回父 chunk 全文 → 去重后返回

```python
def retrieve(query: str, top_k: int = 5) -> list[ParentChunk]:
    # 1. 子仓储向量检索
    child_hits = child_store.search(query=query, top_k=top_k)

    # 2. 收集父 chunk_id 并去重（多个子命中同一父只返回一次父）
    parent_ids = dict.fromkeys(h.parent_chunk_id for h in child_hits)

    # 3. 父仓储批量取回
    parents = parent_store.batch_get(list(parent_ids))

    # 4. 从 StoredToolContent.text 按 offset 切片还原父 chunk 文本
    return [slice_parent_text(p, stored_text) for p in parents]
```

### 4.2 LlamaIndex `AutoMergingRetriever` 模式（可选增强）

当同一父 chunk 的多个子 chunk 同时命中时，合并为完整父 chunk 返回，避免上下文碎片化：

```python
def retrieve_with_auto_merge(query: str, top_k: int = 10, merge_threshold: float = 0.5) -> list[ParentChunk]:
    child_hits = child_store.search(query=query, top_k=top_k)

    # 按 parent_chunk_id 分组
    groups: dict[str, list[ChildHit]] = {}
    for hit in child_hits:
        groups.setdefault(hit.parent_chunk_id, []).append(hit)

    results = []
    for parent_id, hits in groups.items():
        parent = parent_store.get(parent_id)
        child_count = hits[0].child_count
        # 命中数 / 总子数 >= threshold → 合并返回完整父 chunk
        if len(hits) / child_count >= merge_threshold:
            results.append(slice_parent_text(parent, stored_text))
        else:
            # 命中数不足，只返回命中的子 chunk 文本
            results.extend(h.text for h in hits)
    return results
```

`metadata["child_count"]` 和 `metadata["child_index"]` 在此场景下被消费。

## 5. 字段映射建议

### 5.1 父仓储实体

```python
@dataclass(frozen=True, slots=True)
class ParentChunkRecord:
    chunk_id: str           # 父 chunk 唯一标识（主键）
    chunk_index: int        # 全局顺序（回退连续读取用）
    start_offset: int       # 在 StoredToolContent.text 中的起始偏移
    end_offset: int         # 在 StoredToolContent.text 中的结束偏移
    content_hash: str       # 幂等去重
    section_path: tuple[str, ...]  # 章节路径（可选，用于 section selector）
```

### 5.2 子仓储实体

```python
@dataclass(frozen=True, slots=True)
class ChildChunkRecord:
    chunk_id: str           # 子 chunk 唯一标识（主键）
    parent_chunk_id: str    # 跨仓储外键，指向父仓储的 chunk_id
    text: str               # 子 chunk 文本（子仓储需存全文，因为有 overlap）
    embedding: bytes        # 向量
    content_hash: str       # 幂等去重
    child_index: int        # 在父内的序号（AutoMerging 用）
    child_count: int        # 父的总子数（AutoMerging 阈值判断用）
```

## 6. 注意事项

1. **`chunk_index` 全局唯一但跨仓储无意义**：父子 chunk 共享同一 `chunk_index` 序号空间（由 `ChunkingEngine._assign_chunk_indices` 全局连续分配），但拆到双仓储后，各仓储内部应按自己的主键（`chunk_id`）索引，`chunk_index` 仅用于回退场景的连续读取。

2. **`parent_chunk_id` 引用完整性**：子仓储的 `parent_chunk_id` 必须能在父仓储找到对应记录。摄取时应先写父后写子；删除时应先删子后删父。

3. **`content_hash` 幂等**：相同文本内容会产生相同 `content_hash` 和 `chunk_id`，可用于断点续传和去重。但注意：父 chunk 的 `content_hash` 基于父文本，子 chunk 的 `content_hash` 基于子文本，两者不同。

4. **overlap 导致子 chunk 文本不可从父 offset 切片还原**：`RecursiveTextSplitter` 配置了 `child_overlap=100`，子 chunk 之间有重叠，因此子仓储必须独立存储子 chunk 文本，不能像父 chunk 那样只存 offset。

5. **降级路径**：若子仓储不可用，父仓储仍可独立工作（退化为单层 chunk 检索，按 `chunk_index` 连续读取）。反之子仓储不可独立工作（失去上下文）。

6. **`chunk_id` 格式稳定性**：当前格式为 `{prefix}:{level}:{index}:{hash[:16]}`，`prefix` 默认为空。若后续需要跨 content 唯一，应在 finalizer 初始化时传入 `id_prefix=content_id`，此时 `chunk_id` 会变为 `{content_id}:read:0:...`，天然带 content 归属信息，便于跨 content 检索时区分来源。

7. **`SecondaryChunkFinalizer` 对父 chunk 做合并并维护引用关系**：父子场景下不能直接复用 `SingleLayerFinalizer`（它的 `merge_short_tails` 不区分 level，会把短子 chunk 合并到父 chunk 或短父 chunk 合并后子 chunk 孤儿）。拆分出的 `SecondaryChunkFinalizer` 分离父子后只对父 chunk 做 heading-only 合并和短尾合并，通过合并函数返回的 `ChunkMergeResult.remapped_ids`（"被合并的旧 ID → 存活的旧 ID"）更新子 chunk 的 `parent_chunk_id`，保证父子关系在合并后仍然正确。子 chunk 由 `RecursiveTextSplitter` 精切，不参与合并。
