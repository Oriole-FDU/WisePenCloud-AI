# SPECTER2 使用 L2 检索的证据

核对日期：2026-10-29。

## 结论

当前使用未归一化的 SPECTER2 向量，并在 Qdrant 中配置 `Euclid`，具有官方训练说明和官方检索评测代码的依据。**不需要因为使用 L2 而重新生成向量或重新灌库。**

“采用对比学习或三元组损失”本身不能推出“必须使用余弦相似度”。具体距离由模型的训练目标和使用方式决定；SPECTER2 的官方说明明确采用欧氏距离，官方检索评测也直接采用欧氏距离排序。

## 证据一：AI2 明确说明三元组训练使用欧氏距离

来源：[AI2 官方 SPECTER2 技术说明](https://allenai.org/blog/specter2-adapting-scientific-document-embeddings-to-multiple-fields-and-task-formats-c95686c06567)，定位到 **Modeling Deep Dive → Step 1: Pre-Training on Citation Prediction**。

原文关键短句：

> such that the Euclidean distance d is lesser between Q and P+

该段说明：SPECTER2 Base 使用论文查询 Q、正例 P+、负例 P− 构成三元组，使 Q 到正例的欧氏距离小于 Q 到负例的欧氏距离。同一段同时将这一目标称为 contrastive learning objective。

因此，“对比学习”与“使用欧氏距离”在这里同时成立。该段直接描述的是 Base 预训练；下方的官方代码进一步提供检索阶段的依据。

## 证据二：官方检索评测使用负欧氏距离排序

SPECTER2 的[官方模型卡](https://huggingface.co/allenai/specter2)将下游使用和评测指向 [AI2 的 SciRepEval](https://github.com/allenai/scirepeval/blob/e04594e6401cb68fb4ddf6daa801fb219cccc0bb/evaluation/INFERENCE.md)。

本次核对固定到 SciRepEval 提交 `e04594e6401cb68fb4ddf6daa801fb219cccc0bb`，避免后续主分支变化影响复核。

[`evaluation/evaluator.py` 第 32 行](https://github.com/allenai/scirepeval/blob/e04594e6401cb68fb4ddf6daa801fb219cccc0bb/evaluation/evaluator.py#L32)导入：

```python
from sklearn.metrics.pairwise import euclidean_distances
```

同文件的 [`IREvaluator` 第 234—241 行](https://github.com/allenai/scirepeval/blob/e04594e6401cb68fb4ddf6daa801fb219cccc0bb/evaluation/evaluator.py#L234-L241)计算候选分数：

```python
query = np.array([embeddings[qid]])
cids = [cid for cid in qrels[qid] if cid in embeddings]
cands = np.array([embeddings[cid] for cid in qrels[qid] if cid in embeddings])
scores = euclidean_distances(cands, query).flatten()
run[qid] = dict()
for i, cid in enumerate(cids):
    run[qid][cid] = float(-scores[i])
```

距离越小，负距离越大，排序越靠前。这是直接的 L2 检索评测依据。

## 证据三：官方编码路径保留 CLS 原始向量

SPECTER2 官方模型卡的使用示例直接取：

```python
embeddings = output.last_hidden_state[:, 0, :]
```

官方 SciRepEval 的 [`evaluation/encoders.py` 第 164—175 行](https://github.com/allenai/scirepeval/blob/e04594e6401cb68fb4ddf6daa801fb219cccc0bb/evaluation/encoders.py#L164-L175)同样在 CLS 池化后直接返回 embedding，按配置选择是否转为半精度；该路径没有执行单位长度归一化。

这与本地“CLS 池化、保留原始模长”的设置一致。

## 本地配置对应关系

以下是本次核对的本地源码配置，不代表额外查询了运行中的数据库状态。

| 项目 | 当前设置 | 本地依据 |
| --- | --- | --- |
| 向量维度 | 768 | [specter.py 第 21 行](../src/scholar/ml/specter.py#L21) |
| 归一化合同 | `normalize: False` | [specter.py 第 35 行](../src/scholar/ml/specter.py#L35) |
| 实际编码输出 | CLS，转为 float32，直接输出 | [specter.py 第 111 行](../src/scholar/ml/specter.py#L111) |
| Qdrant 创建配置 | `distance=Distance.EUCLID` | [paper_vectors.py 第 64 行](../src/scholar/core/storage/qdrant/paper_vectors.py#L64) |
| 已有集合检查 | 要求 768 维、`Distance.EUCLID` | [paper_vectors.py 第 50 行](../src/scholar/core/storage/qdrant/paper_vectors.py#L50) |
| 查询向量提交 | 直接提交编码结果 `query=vector` | [paper_vectors.py 第 136 行](../src/scholar/core/storage/qdrant/paper_vectors.py#L136) |

## L2 与余弦的区别及结论边界

原始向量满足：

\[
\lVert q-d\rVert_2^2
=\lVert q\rVert_2^2+\lVert d\rVert_2^2
-2\lVert q\rVert_2\lVert d\rVert_2\cos\theta
\]

若双方都归一化为单位向量，则平方 L2 距离为 `2 - 2 × cosine`，两者的精确排序等价。当前保留原始模长，不能直接声称 L2 与余弦等价；改用余弦会改变检索语义。

上述证据支持保留当前 L2 设置，排除了“因为模型使用对比学习，所以 L2 必然配置错误”的判断。它们没有证明 L2 在本项目所有业务查询上都优于余弦；这种比较需要相同数据和查询下的相关性评测。

本次仅补充证据文档，不更改向量、距离配置、集合或迁移任务。
