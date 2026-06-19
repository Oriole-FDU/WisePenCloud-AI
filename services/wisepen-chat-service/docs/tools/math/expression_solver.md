# expression_solver

实现入口：`src/chat/application/tools/math_tools/expression_solver_tool.py`

`expression_solver` 处理基础符号表达式、组合数学和轻量数论任务。它基于 SymPy 和 Python 标准库数学函数，不执行任意代码。

## 何时使用

- 需要表达式化简、展开、因式分解或数值化。
- 需要阶乘、组合数、排列数、最大公约数、最小公倍数或质因数分解。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `task` | `string` | 必填，见支持任务。 |
| `expression` | `string` | `simplify`、`expand`、`factor`、`numeric` 使用。 |
| `variables` | `string[]` | 表达式解析允许的变量名。 |
| `n` | `integer` | 阶乘、组合数、排列数使用。 |
| `k` | `integer` | 组合数、排列数使用。 |
| `integers` | `integer[]` | `gcd`、`lcm` 使用，必须非空。 |
| `integer` | `integer` | `prime_factors` 使用。 |

支持任务：`simplify`、`expand`、`factor`、`numeric`、`factorial`、`combinations`、`permutations`、`gcd`、`lcm`、`prime_factors`。

## 输出

返回普通结构化结果：

- `solver`: `expression_solver`
- `task`: 实际执行的任务
- `exact_result`: 精确结果
- `numeric_result`: `numeric` 会尽量填充 float
- `latex_result`: 可展示 LaTeX，若无法生成则为空

## 边界

- 表达式最长 2000 字符，禁止字符串字面量、`__`、`import` 和 `lambda`。
- `gcd` 和 `lcm` 的 `integers` 必须是非空整数数组，bool 不视为整数。
- 不处理方程求解、微积分、矩阵、统计任务；这些由其它数学工具负责。
- 不读取文件，不访问网络。
