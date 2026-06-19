# stats_solver

实现入口：`src/chat/application/tools/math_tools/stats_solver_tool.py`

`stats_solver` 处理概率、分布函数、描述统计、线性回归和相关性计算。符号概率表达式使用 SymPy，分布、回归和相关性使用 SciPy/NumPy。

## 何时使用

- 需要二项分布、Poisson 分布、正态/t/卡方/F 分布 CDF。
- 需要有限均匀分布下表达式的期望和方差。
- 需要描述统计、简单线性回归或 Pearson/Spearman 相关性。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `task` | `string` | 必填，见支持任务。 |
| `n` / `k` | `integer` | 二项分布或 Poisson 概率使用。 |
| `probability` | `string` | 二项分布成功概率表达式。 |
| `rate` | `number` | Poisson rate。 |
| `point` | `number` | CDF 评估点。 |
| `mean` / `std` | `number` | 正态分布参数，默认 0 和 1。 |
| `df` | `number` | t 或卡方分布自由度。 |
| `dfn` / `dfd` | `number` | F 分布分子/分母自由度。 |
| `expression` | `string` | 有限均匀分布期望方差使用。 |
| `variable` | `string` | 有限均匀分布变量名，默认 `x`。 |
| `lower` / `upper` | `string` | 有限均匀分布整数支撑边界。 |
| `values` | `number[]` | 描述统计样本。 |
| `x_values` / `y_values` | `number[]` | 回归或相关性样本。 |
| `method` | `string` | 相关性方法，`pearson` 或 `spearman`。 |

支持任务：`binomial_prob`、`poisson_prob`、`normal_cdf`、`uniform_expectation_variance`、`descriptive_stats`、`t_cdf`、`chi2_cdf`、`f_cdf`、`linear_regression`、`correlation`。

## 输出

返回普通结构化结果：

- `solver`: `stats_solver`
- `task`: 实际执行的任务
- `exact_result`: 符号或数值结果
- `numeric_result`: 可直接消费的数值结果
- `latex_result`: 可展示 LaTeX，若无法生成则为空

## 边界

- 只做基础概率统计计算，不做数据清洗、文件读取或图表生成。
- `x_values` 和 `y_values` 长度必须一致。
- `descriptive_stats` 使用样本方差和样本标准差；样本量小于等于 1 时方差和标准差为 0。
- 有限均匀分布要求 `upper >= lower`。
- 不执行任意代码，不访问网络。
