# 当前完整数据模型

以下定义直接从当前 Python 类读取。结果序列化不排除默认值、null 或 metadata。

## GraphDirection

```python
class GraphDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"
```

## GraphRelationOption

```python
@dataclass(slots=True)
class GraphRelationOption:
    """类别允许的谓词与方向提示，不表示当前可见事实存在该关系。"""

    relation_type: str
    direction: GraphDirection
```

## GraphNode

```python
@dataclass(slots=True)
class GraphNode:
    """实体的语义身份与单次抽取来源，不携带资源可见性投影。"""

    name: str  # 实体名称；身份仅规范化名称的大小写与空白。
    source_id: str  # 程序绑定的来源 Chunk ID，不是图节点 ID。
    ontology_name: str | None = None  # 本体 domain，参与实体身份。
    category: str | None = None  # 实体类别；本体抽取必须提供合法类别。
    description: str | None = None  # 正文支持的描述，不影响身份。
    relations: list[GraphRelationOption] = field(
        default_factory=list
    )  # 写入时的类别关系提示。

    @property
    def id(self) -> str:
        # 只规范化实体名；缺失类别保留空槽，ontology/category 保持原值。
        normalized_name = " ".join(self.name.strip().casefold().split())
        category = self.category or ""
        if self.ontology_name:
            return f"{self.ontology_name}:{category}:{normalized_name}"
        return f"{category}:{normalized_name}"
```

## GraphEdge

```python
@dataclass(slots=True)
class GraphEdge:
    """有向关系及其来源；端点 ID 与 provenance 分别表达拓扑和来源。"""

    source_id: str  # 起点 GraphNode.id，不是来源 Chunk ID。
    target_id: str  # 终点 GraphNode.id；关系保留方向。
    relation_type: str  # 本体关系谓词，参与身份。
    description: str | None = None  # 正文支持的描述，不影响身份。
    keywords: list[str] | None = None  # 关系语义关键词，可为空。
    source_ids: list[str] = field(default_factory=list)  # 来源 Chunk ID 列表。
    metadata: dict[str, object] = field(default_factory=dict)  # 非 ACL/发布状态。

    @property
    def id(self) -> str:
        # 端点 + 谓词组成关系身份
        return f"{self.source_id}::{self.relation_type}::{self.target_id}"
```

## GraphEntitySource

```python
@dataclass(frozen=True, slots=True)
class GraphEntitySource:
    """一个已授权 Chunk 对实体的描述，不拼接或摘要其他来源。"""

    chunk_id: str
    description: str | None = None
```

## GraphEntity

```python
@dataclass(frozen=True, slots=True)
class GraphEntity:
    """一个逻辑实体只返回一次；不重复名称、本体、类别和本体允许提示。"""

    id: str
    sources: list[GraphEntitySource] = field(default_factory=list)
```

## GraphRelationshipSource

```python
@dataclass(frozen=True, slots=True)
class GraphRelationshipSource:
    """关系的来源相关字段，关键词和 metadata 不跨来源合并。"""

    chunk_id: str
    description: str | None = None
    keywords: list[str] | None = None
    metadata: dict[str, object] = field(default_factory=dict)
```

## GraphRelationship

```python
@dataclass(frozen=True, slots=True)
class GraphRelationship:
    """保留有向谓词拓扑，调用方不需要拆解关系 ID。"""

    id: str
    source_id: str  # 起点实体 ID；来源 Chunk 在 sources 中。
    target_id: str
    relation_type: str
    sources: list[GraphRelationshipSource] = field(default_factory=list)
```

## Chunk

```python
@dataclass(slots=True)
class Chunk:
    """内容 revision 内的分块事实；Transformer 可以原地增强 metadata。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None  # 与 Document 结构一致的 RAG 不透明章节 ID。
    section_path: list[str]
    raw_text: str  # 唯一正文事实，上下文不写回正文。
    content_token_count: int  # Common 对正文的 token 计数，不包含派生上下文。
    anchor_labels: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = self.metadata.get("contextual_context", "").strip()
        # 路径与生成上下文只服务索引；原文、offset 和正文计数保持不变。
        prefixes = [part for part in (">".join(self.section_path), context) if part]
        return "\n\n".join([*prefixes, self.raw_text])
```

## GraphNodeReference

```python
@dataclass(frozen=True, slots=True)
class GraphNodeReference:
    """命中 Chunk 中可供调用方选择的实体；来源身份沿用外层 ChunkHit。"""

    id: str  # GraphNode.id：本体、类别与规范化名称联合确定。
    relations: list[GraphRelationOption] | None = None  # None 未查询；[] 无可见连接。
```

## ChunkHit

```python
@dataclass(frozen=True, slots=True)
class ChunkHit:
    """应用可见的正文命中；score 表示最终排序阶段，不限定为重排分数。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    text: str  # Chunk.raw_text；上下文不拼接到正文，业务 metadata 单独返回。
    rank: int
    score: float
    seed_entities: list[GraphNodeReference] = field(default_factory=list)
    metadata: dict[str, object] = field(
        default_factory=dict
    )  # 当前已授权 Chunk 的 metadata，排除模块常量指定的内部字段。
```

## GraphRetrievalResult

```python
@dataclass(frozen=True, slots=True)
class GraphRetrievalResult:
    """共享图事实与精排原文并列；top_k 和 rank/score 仅作用于 chunks。"""

    entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    chunks: list[ChunkHit] = field(default_factory=list)
```

## GraphTraversalResult

```python
@dataclass(frozen=True, slots=True)
class GraphTraversalResult:
    """一次一跳调用的共享拓扑；每个逻辑对象下保留各来源，不附带 Chunk 原文。"""

    entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
```
