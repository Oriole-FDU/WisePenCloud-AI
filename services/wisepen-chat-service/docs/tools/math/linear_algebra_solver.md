# linear_algebra_solver

实现入口：`src/chat/application/tools/math_tools/linear_algebra_solver_tool.py`

`linear_algebra_solver` 执行确定性的矩阵和线性代数任务。精确任务主要使用 SymPy，SVD、QR 和矩阵整数次幂使用 NumPy 数值计算。

## 何时使用

- 需要行列式、迹、秩、逆、RREF、特征值、线性方程组、矩阵乘法、SVD、QR、零空间或矩阵幂。
- 输入可以表达为 JSON 数组矩阵或向量。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `task` | `string` | 必填，见支持任务。 |
| `matrix` | `array[]` | 主矩阵。元素可为 integer、number 或 string。 |
| `matrix_b` | `array[]` | 矩阵乘法或矩阵右端项线性求解使用。 |
| `vector` | `array` | 向量右端项线性求解使用。 |
| `power` | `integer` | `matrix_power` 使用的整数指数。 |

支持任务：`determinant`、`trace`、`rank`、`inverse`、`rref`、`eigenvalues`、`linear_solve`、`matrix_multiply`、`svd`、`qr_decomposition`、`null_space`、`matrix_power`。

## 输出

返回普通结构化结果：

- `solver`: `linear_algebra_solver`
- `task`: 实际执行的任务
- `exact_result`: 精确结果或结构化数值结果
- `numeric_result`: SVD、QR、矩阵幂等数值任务会填充
- `latex_result`: 可展示 LaTeX，数值分解任务通常为空

## 边界

- 不执行任意 Python 代码，不读取文件，不访问网络。
- `chunk_index`、`cnt_*`、`tfile_*` 等工具运行期引用不适用于本工具。
- `linear_solve` 优先使用 `vector`，未提供时使用 `matrix_b`。
- 矩阵维度、可逆性、数值收敛等错误会包装为工具执行错误。
- 默认超时 20 秒。
