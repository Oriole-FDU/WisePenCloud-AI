# equation_solver

实现入口：`src/chat/application/tools/math_tools/equation_solver_tool.py`

`equation_solver` 处理方程、不等式、数值求根和轻量优化任务。符号部分基于 SymPy，数值求根和优化基于 SciPy。

## 何时使用

- 需要解单个方程、方程组、一元不等式。
- 需要一元数值求根、有界数值最小化或轻量多变量约束最小化。
- 不适合微积分推导或矩阵任务，这些分别由 `calculus_solver` 和 `linear_algebra_solver` 处理。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `task` | `string` | 必填，见支持任务。 |
| `expression` | `string` | 数值求根、优化、不等式等任务使用。 |
| `equation` | `string` | `solve_equation` 使用，例如 `x^2 - 4 = 0`。 |
| `equations` | `string[]` | `solve_system` 使用。 |
| `inequality` | `string` | `solve_inequality` 使用，例如 `x^2 < 4`。 |
| `variable` | `string` | 单变量名，默认 `x`。 |
| `variables` | `string[]` | 方程组和约束优化变量名。 |
| `point` | `string` | 无 bracket 时 `numeric_root` 的初始点。 |
| `lower` / `upper` | `string` | 数值求根 bracket 或有界最小化区间。 |
| `initial_guess` | `number[]` | 约束优化初始向量。 |
| `lower_bounds` / `upper_bounds` | `number[]` | 约束优化变量边界。 |
| `constraints` | `string[]` | 约束优化约束，按 `>= 0` 解释。 |

支持任务：`solve_equation`、`solve_system`、`numeric_root`、`solve_inequality`、`numeric_minimize`、`constrained_minimize`。

## 输出

返回普通结构化结果：

- `solver`: `equation_solver`
- `task`: 实际执行的任务
- `exact_result`: 符号解、关系解、根或优化结果
- `numeric_result`: 数值任务会填充 float 或结构化数值结果
- `latex_result`: 可展示 LaTeX，若无法生成则为空

## 边界

- 表达式解析遵守数学白名单和 2000 字符限制。
- `numeric_root` 使用 `lower` + `upper` 时走 bracket；否则使用 `point` 初始值。
- `constrained_minimize` 要求 `initial_guess` 长度等于变量数。
- 约束表达式不是等式，统一解释为非负约束。
- 不执行任意代码，不处理文件或网络。
