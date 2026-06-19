# calculus_solver

实现入口：`src/chat/application/tools/math_tools/calculus_solver_tool.py`

`calculus_solver` 执行确定性的微积分、级数、常微分方程和 Laplace 变换任务。它基于 SymPy，不是 Python REPL，不执行任意代码，不读取文件，也不访问网络。

## 何时使用

- 需要符号求导、积分、极限、泰勒展开、求和、常微分方程或 Laplace 变换。
- 任务是结构化数学计算，且可用一个 `task` 明确表达。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `task` | `string` | 必填，见支持任务。 |
| `expression` | `string` | 多数任务使用的数学表达式。 |
| `equation` | `string` | `solve_ode` 使用的 ODE 方程，也可由 `expression` 兜底。 |
| `variable` | `string` | 主变量，默认 `x`；`laplace_transform` 默认 `t`。 |
| `variables` | `string[]` | 表达式解析允许的变量名。 |
| `variable2` | `string` | `double_integral` 的第二变量，默认 `y`。 |
| `function` | `string` | `solve_ode` 的待求函数名，默认 `y`。 |
| `transform_variable` | `string` | Laplace 域变量，默认 `s`。 |
| `point` | `string` | 极限点或 Taylor 展开点。 |
| `order` | `integer` | Taylor 展开阶数，默认 6。 |
| `lower_bound` / `upper_bound` | `string` | 定积分或二重积分第一变量上下限。 |
| `lower` / `upper` | `string` | 上下限别名；在 `summation` 中表示求和下标边界。 |
| `lower2` / `upper2` | `string` | 二重积分第二变量上下限。 |

支持任务：`differentiate`、`partial_differentiate`、`integrate`、`definite_integral`、`limit`、`taylor_series`、`summation`、`solve_ode`、`double_integral`、`laplace_transform`。

## 输出

返回普通结构化结果：

- `solver`: `calculus_solver`
- `task`: 实际执行的任务
- `exact_result`: SymPy 精确结果或结构化结果
- `latex_result`: 可展示 LaTeX，若无法生成则为空

## 边界

- 表达式最长 2000 字符，禁止字符串字面量、`__`、`import` 和 `lambda`。
- 只开放数学白名单名称，例如三角函数、指数、对数、平方根、绝对值、`pi`、`e` 和 `oo`。
- 变量名必须是合法 identifier。
- `solve_ode` 仅开放 ODE 解析所需的 `Derivative`、`diff`、`Eq`。
- 工具超时策略由框架控制，默认 20 秒。
