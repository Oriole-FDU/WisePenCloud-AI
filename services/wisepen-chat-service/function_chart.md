# 定稿：Function Plot Tool

定位：

```text
function_plot_tool
= 根据数学表达式绘制函数图像的工具
= 不要求 provenance
= 默认学术风
= 输出 SVG/PNG，可直接在对话中展示/保存
```

主链路：

```text
Tool Input
  ↓
FunctionPlotService
  ↓
SymPy 安全解析
  ↓
NumPy 采样
  ↓
Matplotlib 渲染
  ↓
Tool Result
```

---

# 技术栈

```text
sympy       表达式解析、LaTeX 输出
numpy       函数采样
scipy       极值点 refinement，可选但建议
matplotlib  SVG/PNG 静态渲染，使用 Agg backend
```

不引入：

```text
seaborn
plotly
用户自定义 Python 代码
CLI
FastAPI route
```

---

# Tool Schema

## 输入

```python
class FunctionPlotToolInput(BaseModel):
    plot_kind: Literal["line_2d", "surface_3d", "contour_2d"] = "line_2d"

    expressions: List[str] = Field(min_length=1, max_length=5)

    variables: List[Literal["x", "y", "t"]] = Field(
        default_factory=lambda: ["x"],
        min_length=1,
        max_length=2,
    )

    x_range: List[float] = Field(
        default_factory=lambda: [-10.0, 10.0],
        min_length=2,
        max_length=2,
    )

    y_range: Optional[List[float]] = Field(
        default=None,
        min_length=2,
        max_length=2,
    )

    samples: int = Field(default=1600, ge=200, le=6000)

    output_formats: List[Literal["svg", "png"]] = Field(
        default_factory=lambda: ["svg", "png"],
        min_length=1,
        max_length=2,
    )

    style_profile: Literal[
        "academic_default",
        "academic_slide",
        "academic_compact",
    ] = "academic_default"

    detect_roots: bool = False
    detect_extrema: bool = False

    title: Optional[str] = Field(default=None, max_length=120)
```

## 输出

```python
class FunctionPlotFeature(BaseModel):
    kind: Literal["root", "local_min", "local_max"]
    expression: str
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    method: str
    confidence: Literal["high", "medium", "low"] = "medium"


class FunctionPlotArtifact(BaseModel):
    kind: Literal["svg", "png"]
    mime_type: str
    content: str
    encoding: Literal["text", "base64"]


class FunctionPlotToolResult(BaseModel):
    plot_id: str
    plot_kind: Literal["line_2d", "surface_3d", "contour_2d"]

    latex_expressions: List[str]
    artifacts: List[FunctionPlotArtifact]

    features: List[FunctionPlotFeature] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

这里不要返回 `download_ref`。
SVG 可以直接 text 返回，PNG 用 base64。后续若你们已有统一 file_ref / artifact 协议，再把 `content` 替换成 `file_ref` 即可。

---

# Tool 描述

```text
绘制数学函数图像。适用于用户要求画函数图像、二维曲线、多个函数对比图、二元函数曲面图、等高线图，或要求标注零点/极值点的场景。

该工具只接受数学表达式，不执行用户 Python 代码。默认使用学术风格渲染，输出 SVG/PNG 图像。普通函数图像使用 line_2d；二元函数 z=f(x,y) 使用 surface_3d 或 contour_2d。

不用于统计图、数据表图表、业务图表、可追溯图表或任意 matplotlib 代码执行。
```

---

# 模块结构

```text
function_plot/
├── __init__.py
├── models.py
├── errors.py
├── parser.py
├── sampler.py
├── analyzer.py
├── styles.py
├── renderer.py
├── exporter.py
├── service.py
└── tool.py
```

职责：

```text
models.py    Tool input/result 与内部模型
errors.py    Tool 内部错误
parser.py    表达式解析、安全校验、LaTeX
sampler.py   1D/2D 采样、NaN/Inf/断点处理
analyzer.py  零点、极值点检测
styles.py    academic 风格 rcParams
renderer.py  matplotlib 绘图
exporter.py  SVG/PNG 导出
service.py   纯业务编排
tool.py      对接现有 BaseTool / ToolSpec
```

---

# Tool 内部调用示例

用户说：

```text
画 y = sin(x)/x，范围 -20 到 20，标一下极值点
```

Tool input：

```json
{
  "plot_kind": "line_2d",
  "expressions": ["sin(x) / x"],
  "variables": ["x"],
  "x_range": [-20, 20],
  "samples": 2400,
  "output_formats": ["svg", "png"],
  "style_profile": "academic_default",
  "detect_roots": false,
  "detect_extrema": true,
  "title": "Function Plot of sin(x) / x"
}
```

用户说：

```text
画 sin(x) 和 cos(x) 的对比图
```

Tool input：

```json
{
  "plot_kind": "line_2d",
  "expressions": ["sin(x)", "cos(x)"],
  "variables": ["x"],
  "x_range": [-6.283185307, 6.283185307],
  "samples": 1600,
  "output_formats": ["svg", "png"],
  "style_profile": "academic_default",
  "detect_roots": false,
  "detect_extrema": false,
  "title": "sin(x) and cos(x)"
}
```

用户说：

```text
画 z = sin(sqrt(x^2+y^2)) 的三维图
```

Tool input：

```json
{
  "plot_kind": "surface_3d",
  "expressions": ["sin(sqrt(x^2 + y^2))"],
  "variables": ["x", "y"],
  "x_range": [-8, 8],
  "y_range": [-8, 8],
  "samples": 180,
  "output_formats": ["png"],
  "style_profile": "academic_default",
  "detect_roots": false,
  "detect_extrema": false,
  "title": "3D Surface Plot"
}
```

---

# 安全约束

必须写死：

```text
1. 不允许执行用户 Python 代码。
2. 不允许 eval / exec / import / open / lambda。
3. 不允许 "__"。
4. 不允许 "."、"["、"]"、"{"、"}"。
5. 表达式长度限制 <= 200。
6. 表达式数量 <= 5。
7. 变量只能是 x/y/t。
8. 函数白名单：
   sin cos tan
   asin acos atan
   sinh cosh tanh
   exp log ln sqrt abs
9. 常量白名单：
   pi E
10. samples 限制：
    line_2d <= 6000
    surface_3d / contour_2d <= 250
```

注意：`sympy.parse_expr` 也不能直接裸用。必须用受控 `local_dict` 和预校验。

---

# 渲染规则

## `line_2d`

```text
- 支持 1 到 5 个表达式
- 每个表达式画一条曲线
- x 使用 linspace 采样
- 非 finite 值替换为 NaN
- 大跳变处插入 NaN，避免渐近线乱连
- 多表达式显示 legend
- 输出 SVG/PNG
```

## `surface_3d`

```text
- 只允许一个表达式
- variables 必须是 ["x", "y"]
- z = f(x, y)
- 使用 meshgrid
- 使用 ax.plot_surface
- 默认只输出 PNG
- SVG 可返回 warning：3D SVG 文件可能过大，不推荐
```

## `contour_2d`

```text
- 只允许一个表达式
- variables 必须是 ["x", "y"]
- z = f(x, y)
- 使用 ax.contourf + colorbar
- SVG/PNG 均可
```

---

# 默认样式

```python
ACADEMIC_DEFAULT_RC = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",

    "axes.spines.top": False,
    "axes.spines.right": False,

    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.8,

    "axes.linewidth": 1.0,
    "lines.linewidth": 2.2,

    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "legend.frameon": False,

    "savefig.bbox": "tight",
    "savefig.dpi": 220,
}

ACADEMIC_SLIDE_RC = {
    **ACADEMIC_DEFAULT_RC,
    "font.size": 13,
    "axes.titlesize": 16,
    "axes.labelsize": 13,
    "lines.linewidth": 2.8,
}

ACADEMIC_COMPACT_RC = {
    **ACADEMIC_DEFAULT_RC,
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "lines.linewidth": 1.8,
}
```

---

# Codex 提示词

直接贴这个：

```text
实现一个 Wisepen function_plot tool，不要实现 FastAPI route，不要实现 CLI。

目标：
根据数学表达式绘制函数图像，不要求 provenance。默认学术风，输出 SVG/PNG。支持普通一元函数图像，多函数同图，预留二元函数 surface_3d / contour_2d。可选标注零点和极值点。

技术栈固定：
- sympy：表达式解析、LaTeX 输出
- numpy：数值采样
- scipy：极值点 refinement，可选但建议
- matplotlib：渲染 SVG/PNG，必须使用 Agg backend
- 不使用 seaborn
- 不使用 plotly
- 不执行用户 Python 代码

实现目录：
function_plot/
├── __init__.py
├── models.py
├── errors.py
├── parser.py
├── sampler.py
├── analyzer.py
├── styles.py
├── renderer.py
├── exporter.py
├── service.py
└── tool.py

models.py:
定义 FunctionPlotToolInput、FunctionPlotToolResult、FunctionPlotArtifact、FunctionPlotFeature。
字段按以下 schema 实现：
- plot_kind: "line_2d" | "surface_3d" | "contour_2d"，默认 line_2d
- expressions: List[str]，1-5 个
- variables: List["x"|"y"|"t"]，默认 ["x"]，最多 2 个
- x_range: List[float]，默认 [-10, 10]
- y_range: Optional[List[float]]
- samples: int，默认 1600，范围 200-6000；surface_3d/contour_2d 最大 250
- output_formats: List["svg"|"png"]，默认 ["svg","png"]
- style_profile: "academic_default" | "academic_slide" | "academic_compact"
- detect_roots: bool
- detect_extrema: bool
- title: Optional[str]

FunctionPlotToolResult:
- plot_id
- plot_kind
- latex_expressions
- artifacts: List[FunctionPlotArtifact]
- features: List[FunctionPlotFeature]
- warnings: List[str]
- metadata: Dict[str, Any]

安全要求：
1. 不允许执行用户 Python 代码。
2. parse_expr 前必须做表达式白名单校验。
3. 禁止 "__", "import", "lambda", "open", "exec", "eval", ".", "[", "]", "{", "}"。
4. 表达式长度 <= 200。
5. 只允许变量 x/y/t。
6. 只允许函数 sin/cos/tan/asin/acos/atan/sinh/cosh/tanh/exp/log/ln/sqrt/abs。
7. 只允许常量 pi/E。
8. 将 ^ 规范化为 **，ln 规范化为 log。
9. 使用 sympy parse_expr 时必须传入受控 local_dict/global_dict。
10. 使用 implicit_multiplication_application 支持 2x、sin x。

parser.py:
- 实现 FunctionExpressionParser。
- 输出 ParsedExpression，包含 raw、normalized、sympy_expr、latex、variables。
- 禁止自由符号超出 request.variables。

sampler.py:
- line_2d 使用 np.linspace 采样。
- lambdify(expr, variables, modules=["numpy"])。
- 非 finite 值置 NaN。
- 检测大跳变并插入 NaN，避免渐近线被连起来。
- surface_3d/contour_2d 使用 meshgrid。
- 采样失败时返回 warning，不要让整个工具崩溃，除非所有表达式都失败。

analyzer.py:
- detect_extrema 只在 line_2d 且单 expression 时启用。
- 先用采样数据找局部 min/max 候选，再用 scipy.optimize.minimize_scalar 在局部区间 refine。
- 最多返回 5 个 feature。
- confidence 默认 medium。
- detect_roots 可先用采样符号变化找候选，再用 scipy.optimize.brentq refine。
- 不承诺数学证明。

styles.py:
- 定义 academic_default、academic_slide、academic_compact 三套 rcParams。
- renderer 使用 matplotlib.rc_context。
- 不允许 tool input 直接传 rcParams。

renderer.py:
- 必须设置 matplotlib.use("Agg")。
- line_2d:
  - 多表达式同图
  - label 使用 LaTeX 表达式
  - 多于一条线显示 legend
  - xlabel=x, ylabel=y
  - title 使用 request.title 或默认 Function Plot
- surface_3d:
  - 一个表达式
  - ax.plot_surface(X, Y, Z, cmap="viridis", linewidth=0, antialiased=True)
  - 添加 colorbar
- contour_2d:
  - ax.contourf(X, Y, Z, levels=32, cmap="viridis")
  - 添加 colorbar

exporter.py:
- SVG 输出 text
- PNG 输出 base64
- artifacts 中：
  - svg: mime_type=image/svg+xml, encoding=text
  - png: mime_type=image/png, encoding=base64

service.py:
- 实现 FunctionPlotService.render(input) -> FunctionPlotToolResult。
- 统一编排 parse -> sample -> analyze -> render -> export。
- 生成 plot_id。
- 汇总 warnings。

tool.py:
- 对接项目现有 BaseTool/ToolSpec 机制。
- tool name 使用 "function_plot"。
- description:
  "绘制数学函数图像。适用于普通一元函数图像、多函数对比、二元函数曲面图、等高线图，以及可选零点/极值点标注。不执行用户 Python 代码，不用于统计图或可追溯图表。"
- Tool 返回 FunctionPlotToolResult。
- 不新增 FastAPI route。
- 不新增 CLI。
```


