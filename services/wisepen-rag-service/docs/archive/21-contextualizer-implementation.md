# 21 Contextualizer、标题路径前缀与离线 repair 实施记录

日期：2026-10-12。切片前基线为 RAG **455 passed／17 skipped**。本轮完成可选摄入上下文增强、统一检索文本和维护窗口离线恢复；代码未提交/推送，未对现有数据库执行 repair。保留本轮开始前 Common retrieval/pipeline.py 的格式改动与两个子模块状态。

## 1. 实际落点

| 能力 | 文件 | 实际行为 |
| --- | --- | --- |
| 增强 Transformer | `src/rag/ingestion/transformers/contextualizer.py` | 完整提示文本计数、全文/祖先/own/Chunk 窗口、骨架收缩、输出检查、失败状态及增量指纹 |
| Transformer 导出 | `src/rag/ingestion/transformers/__init__.py` | 公开 Contextualizer，不新增生产装配或策略注册表 |
| 检索文本 | `src/rag/core/schema/content.py` | 路径、上下文、原文空行拼接，正文事实不变 |
| 纯标题骨架 | Common `utils/markdown/outline.py` | show_extra_info=False，仅标题层级；默认输出不变，anchors 可省略 |
| 固定模型与截断拒绝 | Common `utils/ai_clients/instruction.py` | 只读 model；complete 对 finish_reason=length 抛 ValueError，仍返回字符串 |
| 离线恢复 | `scripts/repair_context.py` | 显式资源范围、当前 applied 核对、写前复核、完整 Chunk 替换和全部向量重写 |
| 可阅读装配 | `demo/contextual.md` | 普通 Pipeline、模型初始化、离线配置及 CLI 示例 |

## 2. 材料、预算与失败

Contextualizer 使用已绑定模型的 InstructionClient。默认完整提示文本上限 8000 token，目标上下文 100 字，输出硬上限 150 token，before_ratio=0.5，outline_token_ratio=0.15，实例级模型并发上限 4。默认 Common 离线 o200k_base，注入计数器后所有预算使用同一 tokenizer；没有字符预算、候选正文重新 embedding 或二次模型验真。

一次 transform 建立文档局部结构/父链/子节点、按 chunk_index 排序的 Chunk 索引和文本计数缓存。非空输入必须有匹配 Document；缺失章节、循环父链、非法原文范围、重复 Chunk 身份/索引和资源/revision 不匹配直接失败。输入顺序与原列表/对象保持，排序仅供材料窗口内部定位；结构不写回。

全文作为虚拟根优先检查，模板省略 metadata 与大纲；不建立独立小文档算法。全文不适配时从最高祖先向当前节点检查完整 subtree_span，选择可容纳者。当前 subtree 不适配时使用完整 own_span 原文，对应本章节连续 Chunk 区间作为已消耗种子；own 也不适配时只用目标 Chunk。无标题文档进入目标种子窗口。

窗口邻居按原文 Chunk 顺序拼接、每个 Chunk 只加入一次；可跨同一文档的 section，但不跨 resource/revision。没有 source spans 时不按内容相等去重或伪造 offsets。新增邻居计数用于方向权重，种子不进入 before/after 比例；两侧可加入时选比例误差更小的一侧，平局优先上文。每侧可行性都重新计数完整提示文本，包含目标重复、metadata、大纲、指令和标签；单侧不可容纳仍试另一侧，双侧不可容纳才停止，不宣称最优装箱。

完整骨架首先按独立大纲预算检查；超限先保留当前祖先链，再加入每级邻近兄弟，最后顶层标题。保留必要父链和原树顺序，每次格式化后计数；不截断标题，最小链仍太大则省略。局部模板明确材料不是全文，要求与目标同语言、只据材料、仅返回简短上下文。metadata 用 Pydantic JSON 序列化后确定性渲染，Unicode 保留，未知对象不强制转换字符串。

ContextStatus 是命名 StrEnum，metadata 中保存普通字符串：

- ok：有效非空输出，保存 contextual_context 和 context_version。
- fallback：空目标或完整输入预算不足，无模型调用，清旧上下文与指纹。
- failed：metadata 序列化、供应商调用或输出检查失败，记录日志、清旧值，继续其他 Chunk。

程序错误、输入/结构错误和取消传播。供应商截断由 InstructionClient 拒绝，输出检查只判断非空与 token 上限；语言和真实性仍依赖模型遵循提示词。本轮不承诺检索效果改善或消除幻觉。普通发布依然要求所有必需事实/dense/BM25 写入成功，模型增强失败可降级，必需写入失败不能 apply。

## 3. 增量与 repair 恢复

SHA-256 指纹包含策略版本、client.model、token_counter.name、生成参数和实际完整提示文本，不含 max_concurrency。状态 ok、上下文有效且指纹匹配时跳过模型；缺失/失败/过期/材料或参数变化时重新生成。全文模板本身不使用 metadata，局部模板 metadata 的变化会改变实际提示词及指纹。

脚本以 argparse 接受重复 --resource-id 和/或每行 ID 的 --resource-file，去重保序；不提供资源范围则拒绝运行，不扫描全库。连接与模型配置为 CLI 或 RAG_REPAIR_* 专用环境变量，不加载 Nacos，不启动消费者。内容集合沿用 document_revisions/doc_chunks/resource_publications；向量 collection 默认 document_chunk_vectors_v4，显式核对/初始化现有 Qdrant adapter 配置。模型和 embedding 配置分别初始化 Common 客户端，不在调用时传 model/dimensions。

只修复当前 applied、未 destroyed revision，验证 Document 的资源、版本、revision 与完整正文 hash，读取既有 Chunk、不重新分块。空白文档可以零 Chunk；脚本不根据另一套分块结果推断预期集合。生成后、写入前重新读取完整 publication 状态，变化则跳过所有 Chunk 写入。目标资源处于维护窗口，没有其他 Chunk 写任务；复核不是跨库事务或并发修复协议。

Contextualizer 后按 ChunkFactsIndexer → VectorIndexer 执行。**每次都重写所选文档全部 Chunk 向量**，不因 LLM 指纹复用而跳过投影。已保存上下文但向量失败后，重跑复用上下文并重写完整投影。模型 failed 块仍保存降级事实并索引路径/原文，最终非零退出；数据库/向量错误按文档记录，继续其他显式资源，已成功写入保留。不 stage/apply、不修改 Document、不改变 Chunk 身份、不加外层自动重试。AsyncExitStack 保证已创建的客户端在成功或初始化异常时关闭。

报告 generated/reused/fallback/failed（块）与 skipped/repaired/document_errors（资源）。模型 failed 或文档错误导致退出码 1；预算 fallback 本身不导致非零退出。生成统计包括发布复核后跳过写入的模型工作，不表示该工作已经落库。

## 4. 实际验证

| 范围 | 结果 |
| --- | --- |
| RAG 全服务 | **527 passed／17 skipped** |
| Common Markdown、AI clients、retrieval | **125 passed** |
| Chat 缓存输出工具目录（含目录/章节参考） | **19 passed** |
| 新 Contextualizer 行为用例 | 47 项，纳入全服务通过结果 |
| 离线 repair 行为用例 | 16 项，纳入全服务通过结果 |
| Ruff check / format、compileall、差异检查 | 修改范围检查通过，RAG 源码/测试/scripts Ruff 通过 |

新增覆盖完整提示词预算与 BPE 非可加性、最高祖先、完整 own 种子、无标题/跨章节窗口、非等长邻居与 before_ratio 边界、单侧放不下、大纲收缩、metadata 序列化/体积、非法输入和结构、输出截断/过长/空输出、按块隔离与取消、共享并发、匹配复用及配置/实际输入变化重算。

repair 覆盖 applied/staged/destroyed 筛选、发布状态变化跳过、完整身份错误、数据库异常隔离、模型失败仍落库并非零退出、预算 fallback、零 Chunk、事实/向量部分失败后重跑、全部向量重写、资源参数保序和客户端关闭。普通发布补充真实 Contextualizer + ChunkFactsIndexer + VectorIndexer + Qdrant adapter 的替身装配，验证 dense 与 BM25 文本一致、模型失败可 apply、必要向量失败不 apply。

可复现命令（仓库根目录）：

```powershell
.venv/Scripts/python.exe -m pytest -c services/rag/pyproject.toml `
    --basetemp=D:/WisePenCloud-AI/wisepen_common/WisePenCloud-AI/.pytest_cache/contextual-rag `
    services/rag/tests -q

.venv/Scripts/python.exe -m pytest services/wisepen-common/tests/markdown `
    services/wisepen-common/tests/ai_clients services/wisepen-common/tests/retrieval `
    --asyncio-mode=auto -q

.venv/Scripts/python.exe -m pytest services/wisepen-chat-service/tests/application/tools/output_cache `
    --asyncio-mode=auto -q
```

17 项跳过仍为 11 Mongo、3 Kafka、3 Qdrant 隔离集成测试，未配置测试连接。没有对现有数据库执行 repair，不以替身通过宣称真实模型或数据库联调完成。单独导入脚本验证不加载 rag.core.config.app_settings，CLI --help 验证无连接动作。

## 5. 后续边界

10/11/16 已替换字符预算与 section 内窗口表述，20 保留历史验收并指向本记录。下一切片按 P5 开始图谱生产，依据实际消费方定义 Ontology/抽取/持久化/GraphStorage 合同，再进入图检索。父块、检索效果基线、在线重构建/任务调度和生产配置/生命周期装配继续留后，不预建空 graph Retriever、ParentBuilder 或结果 parents 字段。
