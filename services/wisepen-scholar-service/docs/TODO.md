# 后续迭代与演进规划

当前已正式形成完整的 V2 路线图技术方案，详见：
👉 **[V2 演化路线规划 (v2-roadmap.md)](v2-roadmap.md)**

## V2 核心目标摘要：排序学习 (Learning to Rank)
- **模型选型**：LightGBM / LambdaMART（Listwise / Pairwise 排序）。
- **弱监督数据**：基于 Semantic Scholar API 官方排名采集约 10,000 条 `query-document` 样本对。
- **特征工程**：融合 BM25 多通道文本分、SPECTER2 欧氏距离、RRF 召回基准分、引用数/高影响引用数及标题完全匹配特征。
- **防泄漏划分**：按 `query_id` 严格隔离训练集、验证集与测试集（70 / 15 / 15）。
- **准出基准**：测试集 $\text{nDCG}@10$ 较 V1 基线提升 $\ge 5\%$ 且精排耗时 $\le 10\text{ms}$。
