# Graph Report - .  (2026-08-05)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 3273 nodes · 9322 edges · 150 communities (142 shown, 8 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 922 edges (avg confidence: 0.55)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3bd0e644`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- endpoints/tool.py
- chat/domain/entities/__init__.py
- RagResourceAclProjection
- content_repository.py
- endpoints/navigation.py
- MemoryProvider
- document_link_extract/extractor.py
- RagContentProjection
- vercel_sse_mapper.py
- knowledge_navigation.py
- chat/container.py
- pdf/converter.py
- ingestion/__init__.py
- QueryClient
- ServiceException
- tool_content/services/service.py
- AIAssetClient
- docx/converter.py
- ProviderSearchHttpRequest
- rag/container.py
- tools/core/__init__.py
- application/utils/chunkers/__init__.py
- endpoints/model.py
- retrieval/__init__.py
- RagMaterializedSource
- StoredToolContent
- ToolParametersSchema
- CacheableText
- load_skill_asset_tool.py
- RankCandidate
- SearchResponse
- graph_extraction/__init__.py
- ChatSession
- wisepen_mcp/utils/ranking/_utils.py
- SessionRepository
- application/utils/ranking/_utils.py
- wisepen_mcp/container.py
- Provider
- tools/navigation.py
- graph_projection/projector.py
- content_window_builder.py
- rag/utils/ranking/presets.py
- application/utils/chunkers/markdown/parser.py
- AIAssetClient
- rag/utils/chunkers/markdown/parser.py
- coordinator.py
- agents/__init__.py
- common/cache.py
- ProviderSearcher
- RankCandidate
- wisepen_mcp/utils/ranking/presets.py
- candidate_positions
- RankCandidate
- assign_ranks
- SecurityContextHolder
- McpServiceClient
- ServiceDiscovery
- endpoints/speech.py
- ToolRegistry
- static_page_fetcher.py
- application/utils/ranking/presets.py
- RagServiceClient
- upload_skill_draft_asset.py
- rag/utils/chunkers/__init__.py
- RpcError
- build_knowledge_graph_path_ranking_pipeline
- NacosClientManager
- RankingTokenizer
- assign_ranks
- RankingTokenizer
- RankingTokenizer
- RpcClient
- KnowledgeExtractionWindow
- warn
- windows.py
- ToolExecutionError
- MarkdownChunker
- MarkdownChunker
- assign_ranks
- error
- info
- FileStorageClient
- IErrorCode
- .extract
- wisepen_mcp/main.py
- endpoints/attachment.py
- ToolOutputCache
- WebContentCache
- TextLocator
- GroupRoleType
- rag/utils/chunkers/markdown/locator.py
- .chunk
- EmbeddingClient
- .dispatch
- KeywordPrefilter
- KeywordPrefilter
- KeywordPrefilter
- UrlSecurityError
- application/utils/ranking/diversifiers/mmr_diversifier.py
- wisepen_mcp/utils/ranking/diversifiers/mmr_diversifier.py
- web_fetch/__init__.py
- LiteLLMAdapter
- convert_to_ui_messages
- ResourceAttachmentRef
- .fuse
- tools/resource.py
- .fuse
- .fuse
- renderer.py
- RedisWebContentCacheRepository
- split_markdown_text
- SearchProviderConfig
- wisepen-common
- .score
- .score
- exception_handlers.py
- .upload_skill_asset_content
- tool_content/services/__init__.py
- wisepen_mcp/utils/__init__.py
- rag/api/__init__.py
- rag/application/__init__.py
- rag/core/__init__.py
- rag/utils/__init__.py
- schemas/session.py

## God Nodes (most connected - your core abstractions)
1. `ServiceException` - 79 edges
2. `ChatMessage` - 76 edges
3. `ModelRequestInfo` - 60 edges
4. `ProviderType` - 58 edges
5. `ToolParametersSchema` - 48 edges
6. `ToolDefinition` - 45 edges
7. `ToolInvocation` - 42 edges
8. `ChatErrorCode` - 40 edges
9. `ProviderSearchHttpRequest` - 40 edges
10. `ToolContentService` - 39 edges

## Surprising Connections (you probably didn't know these)
- `wisepen-server-py` --depends_on--> `wisepen-chat-service`  [EXTRACTED]
  pyproject.toml → services/wisepen-chat-service/pyproject.toml
- `wisepen-server-py` --depends_on--> `wisepen-common`  [EXTRACTED]
  pyproject.toml → services/wisepen-common/pyproject.toml
- `wisepen-server-py` --depends_on--> `wisepen-mcp-service`  [EXTRACTED]
  pyproject.toml → services/wisepen-mcp-service/pyproject.toml
- `wisepen-server-py` --depends_on--> `wisepen-rag-service`  [EXTRACTED]
  pyproject.toml → services/wisepen-rag-service/pyproject.toml
- `init_temp_attachment_upload()` --calls--> `info()`  [INFERRED]
  services/wisepen-chat-service/src/chat/api/endpoints/attachment.py → services/wisepen-common/src/common/logger.py

## Import Cycles
- 3-file cycle: `services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/navigation.py -> services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py`
- 4-file cycle: `services/wisepen-chat-service/src/chat/application/tools/core/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/registry.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/mcp_tool_catalog.py -> services/wisepen-chat-service/src/chat/application/tools/core/__init__.py`
- 4-file cycle: `services/wisepen-chat-service/src/chat/application/tools/core/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/registry.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/remote_tool.py -> services/wisepen-chat-service/src/chat/application/tools/core/__init__.py`
- 4-file cycle: `services/wisepen-chat-service/src/chat/application/tools/core/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/registry.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/system_mcp_tool_catalog.py -> services/wisepen-chat-service/src/chat/application/tools/core/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/graph_extraction/models.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/content_indexer.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/projections.py -> services/wisepen-rag-service/src/rag/application/rag/graph_extraction/models.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/graph_extraction/models.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/context_indexing.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/projections.py -> services/wisepen-rag-service/src/rag/application/rag/graph_extraction/models.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/graph_extraction/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/graph_extraction/extractor.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/projections.py -> services/wisepen-rag-service/src/rag/application/rag/graph_projection/models.py -> services/wisepen-rag-service/src/rag/application/rag/graph_extraction/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/evidence/models.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/content_indexer.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/retrieval.py -> services/wisepen-rag-service/src/rag/application/rag/evidence/models.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/evidence/models.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/context_indexing.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/retrieval.py -> services/wisepen-rag-service/src/rag/application/rag/evidence/models.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py -> services/wisepen-rag-service/src/rag/application/rag/retrieval/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/retrieval/retriever.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/navigation.py -> services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/context_indexing.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/retrieval.py -> services/wisepen-rag-service/src/rag/application/rag/section_navigation/models.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/evidence/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/evidence/materializer.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/navigation.py -> services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py -> services/wisepen-rag-service/src/rag/application/rag/evidence/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/evidence/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/evidence/materializer.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/retrieval.py -> services/wisepen-rag-service/src/rag/application/rag/section_navigation/models.py -> services/wisepen-rag-service/src/rag/application/rag/evidence/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/content_indexer.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/retrieval.py -> services/wisepen-rag-service/src/rag/application/rag/section_navigation/models.py -> services/wisepen-rag-service/src/rag/application/rag/ingestion/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/acl/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/acl/authorizer.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/navigation.py -> services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py -> services/wisepen-rag-service/src/rag/application/rag/acl/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/graph_extraction/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/graph_extraction/extractor.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/navigation.py -> services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py -> services/wisepen-rag-service/src/rag/application/rag/graph_extraction/__init__.py`
- 5-file cycle: `services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py -> services/wisepen-rag-service/src/rag/application/rag/section_navigation/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/section_navigation/navigator.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/__init__.py -> services/wisepen-rag-service/src/rag/application/rag/repositories/navigation.py -> services/wisepen-rag-service/src/rag/application/rag/knowledge_navigation.py`
- 5-file cycle: `services/wisepen-chat-service/src/chat/application/tools/core/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/execution/dispatcher.py -> services/wisepen-chat-service/src/chat/application/tools/core/registry.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/mcp_tool_catalog.py -> services/wisepen-chat-service/src/chat/application/tools/core/__init__.py`
- 5-file cycle: `services/wisepen-chat-service/src/chat/application/tools/core/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/execution/executor.py -> services/wisepen-chat-service/src/chat/application/tools/core/registry.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/mcp_tool_catalog.py -> services/wisepen-chat-service/src/chat/application/tools/core/__init__.py`
- 5-file cycle: `services/wisepen-chat-service/src/chat/application/tools/core/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/registry.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/__init__.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/mcp_tool_catalog.py -> services/wisepen-chat-service/src/chat/application/tools/core/mcp/remote_tool.py -> services/wisepen-chat-service/src/chat/application/tools/core/__init__.py`

## Communities (150 total, 8 thin omitted)

### Community 0 - "endpoints/tool.py"
Cohesion: 0.05
Nodes (59): CallToolResult, _build_tool_response(), delete_user_mcp_server(), delete_user_tool_config(), get_user_mcp_server(), get_user_tool_config(), list_user_mcp_servers(), list_user_tools() (+51 more)

### Community 1 - "chat/domain/entities/__init__.py"
Cohesion: 0.09
Nodes (37): 单个 Agent Step 内的事件解释器 - 按到达顺序消费 LLMProvider 传递的 LLMStreamEvent 事件 - 维护…, _StepEventInterpreter, AnthropicAdapter, Any, Anthropic Messages API 适配器, GeminiAdapter, Any, Gemini 官方 Google GenAI SDK 适配器 (+29 more)

### Community 2 - "RagResourceAclProjection"
Cohesion: 0.04
Nodes (51): FieldCondition, _can_view_resource(), Protocol, RagPermissionAuthorizer, RagPermissionIdentity, 以本地 Mongo ACL 投影作为返回结果的最终授权门。, 从 RAG 侧读取 ACL 投影，返回当前用户具有 VIEW 权限的资源 ID。, 根据资源级 ACL 和用户组 ACL 判断用户是否可读。 (+43 more)

### Community 3 - "content_repository.py"
Cohesion: 0.08
Nodes (58): range, RagResourceContentItem, RagResourceContentWindow, 一次批量读取中的单个 page/section 结果。, _document_title(), join_content_parts(), MongoRagExtractionSourceRepository, MongoRagResourceSnapshotRepository (+50 more)

### Community 4 - "endpoints/navigation.py"
Cohesion: 0.07
Nodes (58): cypher(), _cypher_payload(), locate(), _locate_payload(), _permission_scope(), Any, inject, post (+50 more)

### Community 5 - "MemoryProvider"
Cohesion: 0.09
Nodes (17): delete, delete_all_memories(), delete_memory(), list_memories(), get, inject, post, MemoryItemResponse (+9 more)

### Community 6 - "document_link_extract/extractor.py"
Cohesion: 0.06
Nodes (55): DocumentLinkExtractError, DocumentLinkExtractor, DocumentType, PdfParseMethod, Path, Response, RuntimeError, StrEnum (+47 more)

### Community 7 - "RagContentProjection"
Cohesion: 0.06
Nodes (51): RagContentProjection, 资源级别的稳定内容投影，作为多后端 RAG 的统一基础。, _content_revision_id(), prepare_projection_stage(), StrEnum, RagProjectionCheckpoint, RagProjectionStage, RagProjectionStageAction (+43 more)

### Community 8 - "vercel_sse_mapper.py"
Cohesion: 0.07
Nodes (57): chat_completions(), BackgroundTasks, inject, post, 将 coordinator 的 AsyncGenerator 包装成 AI SDK 6.x SSE 格式, _vercel_generator(), ChatRequest, BaseModel (+49 more)

### Community 9 - "knowledge_navigation.py"
Cohesion: 0.05
Nodes (40): KnowledgeGraphCypherRequest, KnowledgeMentionSource, KnowledgeNavigationEdge, KnowledgeNavigationNode, KnowledgeNavigationPath, KnowledgeNavigationState, _path_ranking_text(), RankingPipeline (+32 more)

### Community 10 - "chat/container.py"
Cohesion: 0.03
Nodes (60): AsyncStealthySession, FetcherSession, LogRecord, ChatContextAssembler, 负责短期上下文的全生命周期管理：Redis 热缓存读取与降级回填、上下文裁剪、Prompt 组装, 从 Redis 拉取短期上下文 若返回空列表（缓存过期或异常），则从 MongoDB 回填最近 N 条记录，重建热缓存…, 从 MongoDB 读取当前会话的摘要（如有）, 从后往前累加Token，构建不超过高水位预算的动态滑动窗口。若超过高水位，则触发摘要。 (+52 more)

### Community 11 - "pdf/converter.py"
Cohesion: 0.07
Nodes (45): Connection, get_image_converter(), ImageConverter, Any, AsyncClient, Path, 从平台配置创建并缓存图片 converter。, 通过 PaddleOCR 云端接口将图片转换为 Markdown。 (+37 more)

### Community 12 - "ingestion/__init__.py"
Cohesion: 0.07
Nodes (45): Semaphore, RuntimeError, RagContentIndexer, RagContentIndexingError, RagContentIndexResult, 加载索引写入所需的 ACL 快照。 空文档不会产生向量记录，因此无需强制加载 ACL。, 复用已有向量，并批量生成剩余 Chunk 的稠密向量。, 依赖数据或索引写入未完成，Kafka consumer 应保留 offset。 (+37 more)

### Community 13 - "QueryClient"
Cohesion: 0.07
Nodes (30): BaseSettings, LLMMessage, LLMResponse, Message, ChatBootstrapSettings, wisepen-chat-service 引导配置, BootstrapSettings, 各微服务通用引导配置基类 仅包含从 Nacos 拉取配置之前必须就位的字段 - SERVICE_HOST / SERVICE_PORT - LOG_LEVEL… (+22 more)

### Community 14 - "ServiceException"
Cohesion: 0.09
Nodes (22): IntEnum, MongoModelRepository, Any, datetime, PydanticObjectId, Model / ModelProviderMapping / Provider 的 MongoDB 仓储实现。user_id=None 表示…, Model, ModelFamily (+14 more)

### Community 15 - "tool_content/services/service.py"
Cohesion: 0.14
Nodes (31): ToolContentPageReadItem, ToolContentPageReadResult, ToolContentRangeReadResult, ToolContentReadFailure, ToolContentRegexSearchMatch, ToolContentRegexSearchRequest, ToolContentRegexSearchResult, ToolContentSectionReadItem (+23 more)

### Community 16 - "AIAssetClient"
Cohesion: 0.09
Nodes (23): Any, Any, get_builtin_skill(), get_builtin_skill_meta(), _get_builtin_skill_root(), is_builtin_skill_id(), Path, read_builtin_skill_asset() (+15 more)

### Community 17 - "docx/converter.py"
Cohesion: 0.14
Nodes (32): Element, _attr(), _cell_text(), _child(), _children(), _convert_formula(), DocxConverter, _escape_cell() (+24 more)

### Community 18 - "ProviderSearchHttpRequest"
Cohesion: 0.13
Nodes (15): AnySearchRequest, BaiduQianfanSearchRequest, BaseProviderSearcher, Any, AsyncClient, RuntimeError, HTTP 搜索源基类，子类只声明请求契约、解析器和鉴权头。, SearchProviderError (+7 more)

### Community 19 - "rag/container.py"
Cohesion: 0.07
Nodes (33): KnowledgeGraphIndexer, 根据已生效的正文投影构建并提交知识图谱投影。, _AclRecalculateMessage, _DocumentReadyMessage, DocumentReadyMessageError, Any, BaseModel, Protocol (+25 more)

### Community 20 - "tools/core/__init__.py"
Cohesion: 0.14
Nodes (20): ToolConfigSpec, ToolTimeoutStrategy, ToolDispatcher, Any, ToolExecutor, Any, Protocol, ToolPreflightHook (+12 more)

### Community 21 - "application/utils/chunkers/__init__.py"
Cohesion: 0.12
Nodes (30): BlockKind, Chunk, ChunkDocument, ChunkerKind, ChunkingResult, LocatorKind, StrEnum, Markdown 解析阶段识别出的结构块类型。 (+22 more)

### Community 22 - "endpoints/model.py"
Cohesion: 0.18
Nodes (36): bind_model_provider(), create_user_model(), create_user_provider(), delete_user_model(), delete_user_provider(), list_all_user_models(), list_available_models(), list_user_models_by_provider_id() (+28 more)

### Community 23 - "retrieval/__init__.py"
Cohesion: 0.12
Nodes (24): Condition, NestedCondition, ScoredPoint, 基于 dense/BM25 查询与 ACL 过滤条件召回候选 chunk。, RagCandidateRequest, RagRetrievalCandidate, RagRetrievalRequest, _build_qdrant_group_filters() (+16 more)

### Community 24 - "RagMaterializedSource"
Cohesion: 0.12
Nodes (19): RuntimeError, RagEvidenceMaterializer, RagEvidenceUnavailableError, 回源 Applied SourceRef，并执行完整性与最终权限校验。, Applied retrieval hit 无法从权威 SourceRef 完整回源。, 将检索命中回源为经过最终权限校验的权威证据。, 批量回源检索命中，并恢复每个命中关联的完整证据。, RagMaterializedHit (+11 more)

### Community 25 - "StoredToolContent"
Cohesion: 0.15
Nodes (20): 用于语义检索的 chunk 及其权威原文范围。, StoredToolContent, ToolContentChunk, ToolContentReceipt, Protocol, ToolContentRepository, Chunk, StrEnum (+12 more)

### Community 26 - "ToolParametersSchema"
Cohesion: 0.15
Nodes (11): Any, ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy, SystemMcpToolCatalog, GetHistoricalChatMessagesTool, 历史消息全文检索工具。 Schema 中不暴露 session_id，该字段由系统通过 context 强注入，防止 LLM 幻觉伪造导致越权访问。 (+3 more)

### Community 27 - "CacheableText"
Cohesion: 0.12
Nodes (17): StrEnum, ToolRiskLevel, McpRemoteTool, Any, _restore_tool_return(), CacheableText, 一段待治理正文，以及内容格式和稳定来源标识。, ToolReturn (+9 more)

### Community 28 - "load_skill_asset_tool.py"
Cohesion: 0.14
Nodes (16): AllowedSkillIdCheck, SkillPermissionCheck, build_skill_asset_output_placeholder(), build_skill_output_placeholder(), Any, LoadSkillAssetTool, AIAssetClient, 按 skill_id + 相对路径懒加载 Skill Bundle 内的某个资产（reference / template / 示例等） skill_id… (+8 more)

### Community 29 - "RankCandidate"
Cohesion: 0.18
Nodes (24): KnowledgeRelationType, 从已有节点执行 Cypher 风格的有界关系遍历。, StrEnum, RankCandidate, RankedCandidate, RankQuery, RankRequest, RankResult (+16 more)

### Community 30 - "SearchResponse"
Cohesion: 0.20
Nodes (10): SearchResponse, SearchResult, Any, Any, ProviderSearchRequest, Any, Any, Any (+2 more)

### Community 31 - "graph_extraction/__init__.py"
Cohesion: 0.20
Nodes (26): GraphSchema, _build_schema(), KnowledgeGraphExtractor, LLMInterfaceV2, 根据启用的关系 Profile 构建严格 GraphRAG Schema。, 调用 Neo4j GraphRAG SDK，并只保留能够精确回源的候选图。, ExtractedKnowledgeNode, ExtractedKnowledgeRelation (+18 more)

### Community 32 - "ChatSession"
Cohesion: 0.12
Nodes (8): MongoSessionRepository, datetime, 联合查询，查不到（不存在或不属于该用户）统一抛 SESSION_NOT_FOUND，防止枚举他人 session_id。, 分页拉取用户会话列表，按 updated_at 降序，返回 (当页列表, 总数), ChatSession, Document, 会话实体（Beanie Document，映射到 chat_sessions 集合）, Settings

### Community 33 - "wisepen_mcp/utils/ranking/_utils.py"
Cohesion: 0.10
Nodes (21): StrEnum, 排序信号类型，用于区分不同插件产出的信号来源。, ScoreSignalKind, BM25Scorer, BM25ScorerConfig, RankCandidate, RankingTokenizer, RankQuery (+13 more)

### Community 34 - "SessionRepository"
Cohesion: 0.32
Nodes (13): create_session(), delete_session(), get_session(), get_session_messages(), list_sessions(), pin_session(), get, inject (+5 more)

### Community 35 - "application/utils/ranking/_utils.py"
Cohesion: 0.10
Nodes (21): StrEnum, 排序信号类型，用于区分不同插件产出的信号来源。, ScoreSignalKind, BM25Scorer, BM25ScorerConfig, RankCandidate, RankingTokenizer, RankQuery (+13 more)

### Community 36 - "wisepen_mcp/container.py"
Cohesion: 0.17
Nodes (17): BaseModel, StrEnum, SearchMode, SearchPipelineResult, SearchProviderName, WebSearchCandidate, WebSearchCandidateResult, WebSearchToolResult (+9 more)

### Community 37 - "Provider"
Cohesion: 0.14
Nodes (13): _build_registry(), 工厂函数：组装并返回已注册所有工具的 ToolRegistry 实例。, MongoProviderRepository, Any, PydanticObjectId, 用户 Provider 的 MongoDB 仓储实现。, Provider, Document (+5 more)

### Community 38 - "tools/navigation.py"
Cohesion: 0.14
Nodes (24): CacheableText, get_tool_config_value(), get_tool_context_value(), Context, TypedDict, ToolReturn, KnowledgeNavigationDirection, KnowledgeRelationType (+16 more)

### Community 39 - "graph_projection/projector.py"
Cohesion: 0.15
Nodes (21): KnowledgeGraphIndexAction, KnowledgeGraphIndexingError, KnowledgeGraphIndexResult, RuntimeError, StrEnum, 关系投影依赖未就绪，Kafka 消费应重试当前正文事件。, KnowledgeEdge, KnowledgeGraphProjection (+13 more)

### Community 40 - "content_window_builder.py"
Cohesion: 0.16
Nodes (19): Encoding, bounded_canonical_token_count(), canonical_preview(), count_canonical_tokens(), _encode(), _prefix_from_encoding(), _suffix_from_encoding(), _tokenizer() (+11 more)

### Community 41 - "rag/utils/ranking/presets.py"
Cohesion: 0.13
Nodes (18): _jaccard_similarity(), MmrDiversifier, MmrDiversifierConfig, RankedCandidate, RankingTokenizer, 计算 MMR 使用的 token 集合相似度。, 基于 Jaccard 相似度和同组抑制的多样性控制器。, build_knowledge_search_ranking_pipeline() (+10 more)

### Community 42 - "application/utils/chunkers/markdown/parser.py"
Cohesion: 0.12
Nodes (20): _associate_numbered_labels(), _attach_page_labels(), MarkdownParser, _numbered_anchor(), BlockKind, TextBlock, Token, 解析顶层 token，并维护当前标题栈形成完整 section_path。 标题栈保存 (heading_level, title)。遇到同级或更浅的标题时，… (+12 more)

### Community 43 - "AIAssetClient"
Cohesion: 0.15
Nodes (17): AIAssetClient, FastMCP, register_create_skill_info_tool(), AIAssetClient, FastMCP, register_get_skill_info_tool(), AIAssetClient, FastMCP (+9 more)

### Community 44 - "rag/utils/chunkers/markdown/parser.py"
Cohesion: 0.12
Nodes (20): _associate_numbered_labels(), _attach_page_labels(), MarkdownParser, _numbered_anchor(), BlockKind, TextBlock, Token, 解析顶层 token，并维护当前标题栈形成完整 section_path。 标题栈保存 (heading_level, title)。遇到同级或更浅的标题时，… (+12 more)

### Community 45 - "coordinator.py"
Cohesion: 0.17
Nodes (14): FetchJobHandler, FetchSlot, FetchBatchScheduler, FetchJob, 使用一个共享并发上限调度 static 和 stealthy 抓取。, FetchCoordinator, 协调缓存、静态抓取、浏览器回退、清洗和 PDF 提取。, WebFetchResult (+6 more)

### Community 46 - "agents/__init__.py"
Cohesion: 0.21
Nodes (14): model_validator, build_default_agent(), Agent, AgentMemoryPolicy, AgentModelPolicy, AgentSpec, AgentToolAndSkillPolicy, BaseModel (+6 more)

### Community 47 - "common/cache.py"
Cohesion: 0.15
Nodes (16): _CacheControl, _CachePolicy, _compute_ttl(), _get_freshness_lifetime(), _get_header(), _is_expired(), _parse_cache_control(), _parse_delta_seconds() (+8 more)

### Community 48 - "ProviderSearcher"
Cohesion: 0.14
Nodes (11): ProviderSearcher, Protocol, 可由 Web Search 编排层调用的搜索源。, DdgSearcher, FourGetSearcher, PlatformDefaultSearcher, Any, AsyncClient (+3 more)

### Community 49 - "RankCandidate"
Cohesion: 0.27
Nodes (19): RankCandidate, RankedCandidate, RankQuery, RankRequest, RankResult, RankingPipeline 的统一请求对象。, RankingPipeline 的统一返回对象。, ScoreSignal (+11 more)

### Community 50 - "wisepen_mcp/utils/ranking/presets.py"
Cohesion: 0.13
Nodes (18): 按 signal.weight / (k + rank) 加权倒数排名融合多路信号。, WeightedRrfFusion, RankingPipeline, 按固定阶段编排一次排序，并直接提供同步和异步执行入口。, build_web_search_ranking_pipeline(), RankingPipeline, AsyncZeroEntropy, RankedCandidate (+10 more)

### Community 51 - "candidate_positions"
Cohesion: 0.09
Nodes (19): RankCandidate, RankedCandidate, ScoreSignal, 按 signal.weight / (k + rank) 加权倒数排名融合多路信号。, WeightedRrfFusion, RankCandidate, RankQuery, ScoreSignal (+11 more)

### Community 52 - "RankCandidate"
Cohesion: 0.29
Nodes (18): RankCandidate, RankedCandidate, RankQuery, RankRequest, RankResult, RankingPipeline 的统一请求对象。, ScoreSignal, Diversifier (+10 more)

### Community 53 - "assign_ranks"
Cohesion: 0.13
Nodes (15): RankCandidate, RankedCandidate, ScoreSignal, 按 signal.weight / (k + rank) 加权倒数排名融合多路信号。, WeightedRrfFusion, RankCandidate, RankedCandidate, RankRequest (+7 more)

### Community 54 - "SecurityContextHolder"
Cohesion: 0.21
Nodes (10): 校验当前登录用户与目标 user_id 一致，否则抛出越权异常。, 校验当前用户是目标 Group 的成员，返回其角色；否则抛出越权异常。, 校验当前用户在目标 Group 中的角色在 required_roles 列表内，否则抛出越权异常。, SecurityContextHolder, 工厂函数，返回可用于 Depends 的校验函数。 同时隐含登录校验。 用法：_ =…, 校验当前请求已登录，返回 user_id；否则抛出 PermissionException。 用法：user_id: str =…, require_login(), require_role() (+2 more)

### Community 55 - "McpServiceClient"
Cohesion: 0.14
Nodes (9): BaseHTTPMiddleware, McpToolStructuredContent, McpServiceClient, Any, LoadBalancingStrategy, CommonConstants, SecurityConstants, GrayContextHolder (+1 more)

### Community 56 - "ServiceDiscovery"
Cohesion: 0.17
Nodes (9): Instance, NamingClientProvider, LoadBalancingStrategy, NacosNamingService, 从本地缓存挑一个可用实例 strategy: 覆盖默认策略 exclude: {ip:port} 集合，用于故障转移时跳过已失败的实例, 进程退出时调用 这里只清本地缓存；Nacos SDK 侧的连接由 NacosClientManager 统一负责, Nacos subscribe 回调 如果推送过来的列表为空则保守保留旧缓存，下一次 TTL 触发强制 refresh, Nacos NamingService 的轻量化封装 (+1 more)

### Community 57 - "endpoints/speech.py"
Cohesion: 0.16
Nodes (13): get_speech_credential(), inject, post, GetSpeechCredentialRequest, BaseModel, SpeechCredentialResponse, IflytekSpeechConfig, IflytekSpeechProvider (+5 more)

### Community 58 - "ToolRegistry"
Cohesion: 0.16
Nodes (7): Protocol, Tool, 将工具定义渲染为模型可消费的 function calling schema。, schema_renderer(), Any, 返回全局已注册工具的 schema。 该方法仅用于诊断和测试。运行期 LLM 调用必须使用 ToolScope.schemas()， 确保已应用当前请求的…, ToolRegistry

### Community 59 - "static_page_fetcher.py"
Cohesion: 0.25
Nodes (12): RuntimeError, UrlFetchError, UrlFetchHttpError, UrlFetchNetworkError, UrlFetchUnsupportedUrlError, RawFetchOutput, build_raw_fetch_output(), _decode_body() (+4 more)

### Community 60 - "application/utils/ranking/presets.py"
Cohesion: 0.16
Nodes (14): build_tool_content_semantic_search_pipeline(), RankingPipeline, AsyncZeroEntropy, RankedCandidate, RankQuery, RuntimeError, 基于 ZeroEntropy rerank API 的异步重排器。, ZeroEntropyReranker (+6 more)

### Community 61 - "RagServiceClient"
Cohesion: 0.20
Nodes (11): build_mcp_server(), AIAssetClient, FastMCP, FastMCP, register_rag_tools(), FastMCP, register_navigation_tools(), FastMCP (+3 more)

### Community 62 - "upload_skill_draft_asset.py"
Cohesion: 0.21
Nodes (12): _normalize_asset_name(), _normalize_asset_path(), _parse_draft_asset(), Any, SkillAssetUploadInitAsset, _validate_skill_md(), Any, BaseModel (+4 more)

### Community 63 - "rag/utils/chunkers/__init__.py"
Cohesion: 0.26
Nodes (13): BlockKind, Chunk, ChunkDocument, ChunkerKind, ChunkingResult, LocatorKind, StrEnum, Markdown 解析阶段识别出的结构块类型。 (+5 more)

### Community 64 - "RpcError"
Cohesion: 0.12
Nodes (10): SkillAssetUploadInitAsset, SkillAssetUploadInitResult, SkillInfo, BaseException, Exception, RpcError, ServiceUnavailableError, SkillAssetUploadInitAsset (+2 more)

### Community 65 - "build_knowledge_graph_path_ranking_pipeline"
Cohesion: 0.15
Nodes (14): build_knowledge_graph_path_ranking_pipeline(), RankingPipeline, 构造知识图谱路径检索的词法匹配与重排预设。, BM25Scorer, BM25ScorerConfig, RankingTokenizer, 基于 candidate.text 的 BM25 词法相关性打分器。, DenseVectorScorer (+6 more)

### Community 66 - "NacosClientManager"
Cohesion: 0.17
Nodes (7): NacosConfigService, NacosClientManager, NacosNamingService, 从 Nacos 注销当前服务实例（优雅关闭）。, Nacos 客户端管理器 (单例类) 封装了 Nacos 的配置拉取、配置监听、服务注册与注销逻辑, 注册到 Nacos 时使用的 IP 优先级 NACOS_REGISTER_IP ＞ SERVICE_HOST（非回环地址） ＞…, 基于 Nacos 的客户端服务发现 + 客户端侧负载均衡

### Community 67 - "RankingTokenizer"
Cohesion: 0.18
Nodes (8): RankingTokenizer, 面向 BM25 / lexical ranking 的 tokenizer 基类。 子类通过实现 _tokenize_cjk 接入不同中文分词器。, 将文本切分为面向排序的 token 序列。, JiebaRankingTokenizer, RankingTokenizer, 使用 jieba 搜索模式分词的中文 tokenizer。, 使用 THULAC 分词的中文 tokenizer。, ThuLacRankingTokenizer

### Community 68 - "assign_ranks"
Cohesion: 0.15
Nodes (13): RankCandidate, RankedCandidate, ScoreSignal, RankCandidate, RankedCandidate, RankRequest, RankResult, 使用外部信号、scorer 或输入顺序构造初始排名。 (+5 more)

### Community 69 - "RankingTokenizer"
Cohesion: 0.18
Nodes (8): RankingTokenizer, 面向 BM25 / lexical ranking 的 tokenizer 基类。 子类通过实现 _tokenize_cjk 接入不同中文分词器。, 将文本切分为面向排序的 token 序列。, JiebaRankingTokenizer, RankingTokenizer, 使用 jieba 搜索模式分词的中文 tokenizer。, 使用 THULAC 分词的中文 tokenizer。, ThuLacRankingTokenizer

### Community 70 - "RankingTokenizer"
Cohesion: 0.18
Nodes (8): RankingTokenizer, 面向 BM25 / lexical ranking 的 tokenizer 基类。 子类通过实现 _tokenize_cjk 接入不同中文分词器。, 将文本切分为面向排序的 token 序列。, JiebaRankingTokenizer, RankingTokenizer, 使用 jieba 搜索模式分词的中文 tokenizer。, 使用 THULAC 分词的中文 tokenizer。, ThuLacRankingTokenizer

### Community 71 - "RpcClient"
Cohesion: 0.17
Nodes (6): Limits, Any, LoadBalancingStrategy, 基于 Nacos ServiceDiscovery 的通用内部 RPC 客户端, RPC 客户端，用于发起内部服务的 HTTP 调用, RpcClient

### Community 72 - "KnowledgeExtractionWindow"
Cohesion: 0.17
Nodes (14): Neo4jNode, KnowledgeExtractionWindow, 知识抽取窗口，作为 LLM 的最小工作单元。, _locate_evidence(), _map_local_span(), _optional_string(), KnowledgeRelationType, Neo4jGraph (+6 more)

### Community 73 - "warn"
Cohesion: 0.21
Nodes (14): debug(), _emit(), Any, warn(), warning(), emit_log(), instrument_fastapi_app(), normalize_attributes() (+6 more)

### Community 74 - "windows.py"
Cohesion: 0.19
Nodes (14): KnowledgeExtractionSource, KnowledgeWindowSourceSpan, 窗口内父块 raw_text 区间到原文 Markdown 区间的双向映射。, 当前 applied revision 的图抽取输入。, build_extraction_windows(), _clip_mappings(), _parent_window_ranges(), SourceSpan (+6 more)

### Community 75 - "ToolExecutionError"
Cohesion: 0.13
Nodes (9): Exception, ToolExecutionError, Any, Any, 按 Section 路径批量读取单文档权威原文。, 按 offset 区间读取单文档权威原文。, ToolContentReadRangeTool, ToolContentReadSectionsTool (+1 more)

### Community 76 - "MarkdownChunker"
Cohesion: 0.19
Nodes (10): MarkdownChunker, Chunk, ChunkDocument, ChunkingResult, TextBlock, 对超长 block 进行递归拆分，拆分后 offset 从 block 内部坐标平移回原文坐标。, 从选中的 blocks 构建一个 Chunk。 chunk 的 start/end_offset 取首末 span 的边界（可能不连续），…, 按标题语义把 Markdown 结构块投影为检索块。 (+2 more)

### Community 77 - "MarkdownChunker"
Cohesion: 0.19
Nodes (10): MarkdownChunker, Chunk, ChunkDocument, ChunkingResult, TextBlock, 对超长 block 进行递归拆分，拆分后 offset 从 block 内部坐标平移回原文坐标。, 从选中的 blocks 构建一个 Chunk。 chunk 的 start/end_offset 取首末 span 的边界（可能不连续），…, 按标题语义把 Markdown 结构块投影为检索块。 (+2 more)

### Community 78 - "assign_ranks"
Cohesion: 0.20
Nodes (12): RankCandidate, RankedCandidate, RankRequest, RankResult, RankingPipeline, 使用外部信号、scorer 或输入顺序构造初始排名。, 按固定阶段编排一次排序，并直接提供同步和异步执行入口。, 同步执行优先过滤、打分融合和 MMR，不允许异步重排器。 (+4 more)

### Community 79 - "error"
Cohesion: 0.17
Nodes (6): memoryview, MessageHandler, KafkaConsumerClient, Any, error(), BaseException

### Community 80 - "info"
Cohesion: 0.23
Nodes (6): OssFileLoader, Path, 经 wisepen-file-storage-service 颁发的预签名 URL 从 OSS 拉取 Object 把 Object…, lifespan(), FastAPI, info()

### Community 81 - "FileStorageClient"
Cohesion: 0.19
Nodes (5): Any, StorageRecord, UploadInitResponse, FileStorageClient, wisepen-file-storage-service 的 Python 侧 typed facade Java RemoteStorageService…

### Community 82 - "IErrorCode"
Cohesion: 0.30
Nodes (8): IdentityType, IErrorCode, Enum, ResultCode, PageResult, BaseModel, R, 通用分页结果，对齐 Java PageResult<T>

### Community 83 - ".extract"
Cohesion: 0.19
Nodes (11): decode_derived_graph(), encode_derived_graph(), Neo4jGraph, 将父块窗口绑定的图转换为可复用派生结果格式。, 替换节点 ID 前缀，并校验来源是否合法。, _replace_node_prefix(), slice_window_graph(), Neo4jGraph (+3 more)

### Community 84 - "wisepen_mcp/main.py"
Cohesion: 0.20
Nodes (10): AppSettings, load_settings(), BaseModel, 在新线程的独立事件循环中执行协程，兼容 uvicorn 启动时已有运行中事件循环的场景。, _run_async(), McpBootstrapSettings, health(), lifespan() (+2 more)

### Community 85 - "endpoints/attachment.py"
Cohesion: 0.35
Nodes (11): add_resource_attachments(), delete_attachment(), init_temp_attachment_upload(), inject, post, AddResourceAttachmentsRequest, DeleteAttachmentRequest, InitUploadRequest (+3 more)

### Community 86 - "ToolOutputCache"
Cohesion: 0.23
Nodes (7): Any, CacheableText, ToolReturn, 逐段存储大文本，并返回成功写入的内容回执。, 将 ToolReturn 中可缓存的大文本存储，并生成模型可见的内容预览。, 将可缓存文本附加到可见结果，并补充后续读取所需的凭证字段。, ToolOutputCache

### Community 87 - "WebContentCache"
Cohesion: 0.21
Nodes (8): Protocol, Web 工具共享的 URL 内容缓存；缓存故障时由调用方继续实时处理。, WebContentCache, WebContentCacheRepository, 复用 static -> stealthy 抓取链路，按 BFS 递归爬取 HTML。, WebCrawler, Protocol, WebFetcher

### Community 88 - "TextLocator"
Cohesion: 0.31
Nodes (12): _anchor_locators(), build_markdown_locators(), _page_locators(), TextBlock, TextLocator, 基于 Markdown 结构块构建章节、页码和锚点原文定位。, 章节范围包含标题本身，并延伸到下一个同级/更高级标题之前。 例如：H1 范围从 H1 起始到下一个 H1；H2 范围从 H2 起始到下一个同级 H2。, 每个页定位从当前 marker 开始，到下一个 marker 之前结束。 (+4 more)

### Community 89 - "GroupRoleType"
Cohesion: 0.27
Nodes (5): Any, ResourceItemInfo, ResourcePermission, _string_tuple(), GroupRoleType

### Community 90 - "rag/utils/chunkers/markdown/locator.py"
Cohesion: 0.31
Nodes (12): _anchor_locators(), build_markdown_locators(), _page_locators(), TextBlock, TextLocator, 基于 Markdown 结构块构建章节、页码和锚点原文定位。, 章节范围包含标题本身，并延伸到下一个同级/更高级标题之前。 例如：H1 范围从 H1 起始到下一个 H1；H2 范围从 H2 起始到下一个同级 H2。, 每个页定位从当前 marker 开始，到下一个 marker 之前结束。 (+4 more)

### Community 91 - ".chunk"
Cohesion: 0.17
Nodes (9): PlainTextChunker, PlainTextChunkerConfig, ChunkDocument, ChunkingResult, 按语言无关分隔符递归切分纯文本，并保留原文位置。, 生成无结构 locator 的普通文本分块。, assign_chunk_ids(), Chunk (+1 more)

### Community 92 - "EmbeddingClient"
Cohesion: 0.27
Nodes (7): EmbeddingInput, build_embedding_client(), EmbeddingClient, EmbeddingResult, Any, Embedding 调用结果，屏蔽底层 SDK 响应结构。, 面向 OpenAI-compatible embedding API 的同步/异步客户端。

### Community 93 - ".dispatch"
Cohesion: 0.20
Nodes (4): Request, Any, 从网关透传的 int code 转换为 IdentityType 枚举存储。, 将 JSON 字符串反序列化，code → GroupRoleType 枚举，存入上下文。

### Community 94 - "KeywordPrefilter"
Cohesion: 0.30
Nodes (5): KeywordPrefilter, KeywordPrefilterConfig, RankCandidate, RankQuery, 基于 query metadata 中 keywords 的硬过滤器。

### Community 95 - "KeywordPrefilter"
Cohesion: 0.30
Nodes (5): KeywordPrefilter, KeywordPrefilterConfig, RankCandidate, RankQuery, 基于 query metadata 中 keywords 的硬过滤器。

### Community 96 - "KeywordPrefilter"
Cohesion: 0.30
Nodes (5): KeywordPrefilter, KeywordPrefilterConfig, RankCandidate, RankQuery, 基于 query metadata 中 keywords 的硬过滤器。

### Community 97 - "UrlSecurityError"
Cohesion: 0.33
Nodes (10): IPAddress, ValueError, 通过 DNS over HTTPS 获取 hostname 的解析结果。, 校验 URL 是否可作为公网 HTTP(S) 请求目标。, 解析 hostname，并确认解析结果均为公网地址。, _reject_blocked_ip(), _resolve_public_host_ips(), _resolve_with_doh() (+2 more)

### Community 98 - "application/utils/ranking/diversifiers/mmr_diversifier.py"
Cohesion: 0.25
Nodes (7): _jaccard_similarity(), MmrDiversifier, MmrDiversifierConfig, RankedCandidate, RankingTokenizer, 计算 MMR 使用的 token 集合相似度。, 基于 Jaccard 相似度和同组抑制的多样性控制器。

### Community 99 - "wisepen_mcp/utils/ranking/diversifiers/mmr_diversifier.py"
Cohesion: 0.25
Nodes (7): _jaccard_similarity(), MmrDiversifier, MmrDiversifierConfig, RankedCandidate, RankingTokenizer, 计算 MMR 使用的 token 集合相似度。, 基于 Jaccard 相似度和同组抑制的多样性控制器。

### Community 100 - "web_fetch/__init__.py"
Cohesion: 0.24
Nodes (6): Any, 静态 HTML 页面抓取器。session 生命周期由容器管理。, StaticPageFetcher, Any, 浏览器 HTML 页面抓取器。session 生命周期由容器管理。, StealthyPageFetcher

### Community 101 - "LiteLLMAdapter"
Cohesion: 0.36
Nodes (3): LiteLLMAdapter, Any, 使用 LiteLLM 库直接在进程内进行非重点模型和普通 OpenAI-compatible fallback 调用 api_base / api_key…

### Community 102 - "convert_to_ui_messages"
Cohesion: 0.36
Nodes (7): _build_assistant_ui_message(), _build_user_ui_message(), convert_to_ui_messages(), Any, 将 MongoDB 中按 OpenAI 格式存储的 ChatMessage 列表转换为 Vercel AI SDK 6.x UIMessage 格式（带…, 将按 created_at 排序的 ChatMessage[] 分组并转换为 UIMessage[]。 分组规则： - 每条 user 消息独立成一个…, 将一组连续的 assistant + tool 消息合并为单个 assistant UIMessage。 遍历顺序即 DB 的 created_at…

### Community 103 - "ResourceAttachmentRef"
Cohesion: 0.19
Nodes (7): Any, AttachmentRef, BaseModel, ResourceAttachmentRef, TemporaryAttachmentRef, ABC, datetime

### Community 104 - ".fuse"
Cohesion: 0.31
Nodes (4): RankCandidate, RankedCandidate, RankQuery, ScoreSignal

### Community 105 - "tools/resource.py"
Cohesion: 0.44
Nodes (8): preview(), _item_payload(), Any, CacheableText, _render_read_result(), _render_structure_result(), _section_payload(), _window_payload()

### Community 106 - ".fuse"
Cohesion: 0.31
Nodes (4): RankCandidate, RankedCandidate, RankQuery, ScoreSignal

### Community 107 - ".fuse"
Cohesion: 0.31
Nodes (4): RankCandidate, RankedCandidate, RankQuery, ScoreSignal

### Community 108 - "renderer.py"
Cohesion: 0.46
Nodes (7): _is_empty_json_value(), _json_default(), Any, 补充 orjson 默认不支持的常见工具返回类型。, 将常见返回值编码为 JSON，不支持的对象降级为原始文本表达。, _remove_empty_json_values(), render_tool_result()

### Community 109 - "RedisWebContentCacheRepository"
Cohesion: 0.36
Nodes (3): Redis, RedisRepository, RedisWebContentCacheRepository

### Community 110 - "split_markdown_text"
Cohesion: 0.39
Nodes (8): ChunkDocument, TextBlock, 按段落、换行、句子到字符的优先级递归切分纯文本。, 切分单个 oversized Markdown block，优先使用结构化分隔符。, 适配第三方递归切分器，并恢复每段文本在原文中的准确位置。, split_markdown_text(), split_plain_text(), _split_recursive_text()

### Community 111 - "SearchProviderConfig"
Cohesion: 0.22
Nodes (14): AnySearchSearcher, AsyncClient, BaiduQianfanSearcher, AsyncClient, SearchProviderConfig, SearchProviderCredentialError, ExaSearcher, AsyncClient (+6 more)

### Community 112 - "wisepen-common"
Cohesion: 0.70
Nodes (5): wisepen-chat-service, wisepen-common, wisepen-mcp-service, wisepen-rag-service, wisepen-server-py

### Community 113 - ".score"
Cohesion: 0.50
Nodes (3): RankCandidate, RankQuery, ScoreSignal

### Community 114 - ".score"
Cohesion: 0.50
Nodes (3): RankCandidate, RankQuery, ScoreSignal

### Community 149 - "schemas/session.py"
Cohesion: 0.26
Nodes (12): CreateSessionRequest, PinSessionRequest, BaseModel, Vercel AI SDK 6.x UIMessage 格式，用于 initialMessages。 所有内容（文本、推理、工具调用）均在 parts…, Vercel AI SDK 6.x UIMessage 的单个 part, RenameSessionRequest, ResourceAttachmentRefResponse, SessionResponse (+4 more)

## Knowledge Gaps
- **5 isolated node(s):** `Settings`, `Settings`, `Settings`, `Settings`, `Settings`
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ServiceException` connect `ServiceException` to `endpoints/tool.py`, `chat/domain/entities/__init__.py`, `SessionRepository`, `ChatSession`, `RpcError`, `MemoryProvider`, `Provider`, `LiteLLMAdapter`, `vercel_sse_mapper.py`, `tools/navigation.py`, `chat/container.py`, `wisepen_mcp/container.py`, `endpoints/navigation.py`, `SecurityContextHolder`, `endpoints/speech.py`, `RagServiceClient`, `upload_skill_draft_asset.py`?**
  _High betweenness centrality (0.120) - this node is a cross-community bridge._
- **Why does `get_pdf_converter()` connect `pdf/converter.py` to `document_link_extract/extractor.py`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Why does `DocumentLinkExtractor` connect `document_link_extract/extractor.py` to `chat/container.py`, `ToolParametersSchema`, `CacheableText`, `WebContentCache`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 76 inferred relationships involving `ServiceException` (e.g. with `delete_all_memories()` and `delete_memory()`) actually correct?**
  _`ServiceException` has 76 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ChatMessage` (e.g. with `ProviderType` and `ModelRequestInfo`) actually correct?**
  _`ChatMessage` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `ModelRequestInfo` (e.g. with `ChatTurnFinalizer` and `LLMProviderResolver`) actually correct?**
  _`ModelRequestInfo` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `ProviderType` (e.g. with `AvailableModelsResponse` and `BindModelProviderRequest`) actually correct?**
  _`ProviderType` has 35 INFERRED edges - model-reasoned connections that need verification._