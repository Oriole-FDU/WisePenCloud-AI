# math_solver 能力边界说明

本文档描述当前 `math_solver` 的真实能力范围、明确不支持的任务，以及后续接入沙箱后的演进方向。

当前模块保持两类工具：

1. `python_math_solver`：面向普通数学计算，使用固定 task 调度到 SymPy / SciPy。
2. `sage_math_solver`：面向高级精确数学，向 SageMath worker 发送结构化 task payload。

两个工具都不执行任意代码。调用方只能提交 schema 中声明的结构化字段，由服务内部按 `task` 分派到受控实现。

## python_math_solver

`python_math_solver` 适合普通符号计算、矩阵计算、组合概率和部分数值计算。它不调用 SageMath，也不执行任意 Python 代码。

### 支持任务

| 类型 | task |
| --- | --- |
| 符号表达式 | `simplify`, `expand`, `factor`, `numeric` |
| 方程 | `solve_equation`, `solve_system` |
| 微积分 | `differentiate`, `integrate`, `definite_integral`, `limit`, `taylor_series`, `summation` |
| 矩阵 | `matrix_determinant`, `matrix_trace`, `matrix_rank`, `matrix_inverse`, `matrix_rref`, `matrix_eigenvalues`, `matrix_solve`, `matrix_multiply` |
| 组合 | `factorial`, `binomial`, `permutation` |
| 概率统计 | `binomial_probability`, `poisson_probability`, `normal_cdf`, `expectation`, `variance` |
| 数值算法 | `numeric_root`, `numeric_minimize` |

### 表达式边界

Python runtime 的表达式解析经过受控解析器：

1. 表达式最大长度为 2000 字符。
2. 禁止 `__`、`import`、`lambda`。
3. 禁止字符串字面量。
4. 只开放常见数学函数和常量，如 `sin`、`cos`、`tan`、`exp`、`log`、`sqrt`、`Abs`、`pi`、`E`、`oo`。
5. 变量名必须是合法 identifier。

这意味着它适合计算数学表达式，不适合作为 Python REPL、脚本执行器或数据分析环境。

### 不支持任务

`python_math_solver` 明确不支持：

1. 任意 Python 代码执行。
2. 文件读写、网络访问、系统命令、包安装。
3. pandas / dataframe 数据处理。
4. 绘图、图像输出、交互式 notebook。
5. 需要 SageMath 的高级精确数学，例如有限域、多项式环、Smith/Hermite 标准形。
6. 形式化证明或定理证明。
7. 超出 schema 的自定义算法流程。

遇到 SageMath 专属任务时，tool 会返回错误并提示使用 `sage_math_solver`。

## sage_math_solver

`sage_math_solver` 适合高级精确数学，当前通过 HTTP 调用 SageMath worker 的 `/compute` 接口。它只传递结构化 task payload，不执行调用方提交的任意 Sage 代码。

### 支持任务

| 类型 | task |
| --- | --- |
| 模运算 | `modular_arithmetic`, `modular_inverse`, `crt` |
| 整数论 | `gcd`, `lcm`, `xgcd`, `prime_factorization`, `is_prime`, `next_prime`, `euler_phi`, `divisors`, `sigma`, `moebius` |
| 有限域 | `finite_field_basic`, `finite_field_operation` |
| 域上多项式 | `polynomial_factor_over_field`, `polynomial_roots_over_field`, `polynomial_gcd_over_field`, `polynomial_is_irreducible_over_field` |
| 多项式不变量 | `polynomial_resultant`, `polynomial_discriminant`, `polynomial_squarefree_decomposition`, `polynomial_quotient_remainder` |
| 高级矩阵 | `matrix_smith_form`, `matrix_hermite_form`, `matrix_minimal_polynomial`, `matrix_characteristic_polynomial`, `matrix_kernel`, `matrix_image` |

### 不支持任务

`sage_math_solver` 明确不支持：

1. 任意 Sage 代码执行。
2. 任意 Python 代码执行。
3. 普通符号计算和数值计算，这些应交给 `python_math_solver`。
4. 文件读写、网络访问、系统命令、包安装。
5. 绘图、交互式 notebook、长任务工作流。
6. 调用方自定义算法或多步骤程序。
7. 形式化证明或机器可验证证明。

遇到 Python runtime 专属任务时，tool 会返回错误并提示使用 `python_math_solver`。

## 调用选择建议

| 需求 | 推荐工具 |
| --- | --- |
| 化简、展开、因式分解普通表达式 | `python_math_solver` |
| 求导、积分、极限、泰勒展开 | `python_math_solver` |
| 求解普通方程或方程组 | `python_math_solver` |
| 常规矩阵行列式、逆、秩、RREF、特征值 | `python_math_solver` |
| 概率质量、正态 CDF、有限均匀分布期望/方差 | `python_math_solver` |
| 数值求根或单变量有界最小化 | `python_math_solver` |
| 有限域或模运算 | `sage_math_solver` |
| 数论函数、素性、CRT、因数分解 | `sage_math_solver` |
| 域上多项式分解、根、GCD、不可约性 | `sage_math_solver` |
| Smith/Hermite 标准形、最小多项式、核、像 | `sage_math_solver` |
| 需要写自定义算法代码 | 当前不支持 |

## 当前设计取舍

当前实现有意保持扁平：

1. tool 层只暴露 schema、上下文校验和错误格式化。
2. service 层只做 task 分组和运行时调度。
3. engine / worker 层承载具体数学计算。
4. 不把 math solver 伪装成通用代码执行环境。

这使得工具可控、可审计，也降低了任意代码执行带来的安全风险。

## 沙箱演进点

如果后续接入可信沙箱，可以新增第三类能力：由 AI 生成短代码并在隔离沙箱中执行数学任务。

推荐把它设计成独立工具或独立 runtime，例如：

```text
ai_math_code_solver
```

或：

```text
math_solver/
  services/
    sandbox_runtime/
```

该能力适合处理当前两个结构化 solver 覆盖不到的问题：

1. 需要多步骤自定义算法。
2. 需要循环、递推、搜索、枚举或动态规划。
3. 需要临时组合 SymPy / NumPy / SciPy / SageMath 的多个 API。
4. 需要生成中间表格或验证性计算。

接入时必须满足以下边界：

1. 代码只在沙箱中执行，不在主服务进程执行。
2. 沙箱必须限制 CPU、内存、运行时长、文件系统和网络访问。
3. 输入、生成代码、stdout、stderr、产物路径都要进入结构化响应，便于审计。
4. 下载或产物同步交给沙箱临时文件机制，主服务只接收受控引用。
5. 结构化 solver 仍应作为优先路径；只有 task 无法表达时才进入代码执行路径。

这个演进点不应改造现有 `python_math_solver` / `sage_math_solver` 的安全边界，而应作为更高风险、更强能力的独立运行形态接入。

