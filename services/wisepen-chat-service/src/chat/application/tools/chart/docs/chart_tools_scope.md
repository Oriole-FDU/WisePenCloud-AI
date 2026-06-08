# Chart Tools Scope

本文档说明当前 chart namespace 下两个正式工具的职责边界：

- `function_plot`
- `stat_chart`

旧的 `quick_chart_from_table`、`quick_function_plot`、`traceable_chart_from_note` 已下线。当前 chart 工具不再走旧的临时文件型 quick chart service，而是直接返回结构化 result 和 inline SVG/PNG artifacts，方便前端即时预览，也避免旧工具族继续膨胀。

## 总体原则

我们做的是两个受控绘图工具：

```text
function_plot: 数学表达式 -> 函数图像
stat_chart: 表格数据 -> 统计图
```

共同原则：

- 不执行用户 Python 代码。
- 不接收自由 matplotlib/seaborn kwargs。
- 不让 LLM 写绘图代码。
- 默认学术风格。
- 输出 SVG text 和 PNG base64 artifact。
- tool schema 是 LLM 能填写的唯一绘图协议。
- service 层接收 tool 层构造的可信 request，只做业务编排。
- 具体 parser、dataframe、validator、renderer、exporter 由 DI 容器显式注入。

## 动态暴露

两个工具都是普通业务工具，默认 deferred，通过 `tool_search` 按 namespace 暴露。

```python
function_plot.namespaces == ("math_solver", "chart")
stat_chart.namespaces == ("chart",)
```

含义：

- 用户要求“画函数图像”“画 sin(x)”时，`function_plot` 可通过 `math_solver` 或 `chart` namespace 被发现。
- 用户提供表格数据并要求统计图、箱线图、散点图、相关矩阵时，`stat_chart` 通过 `chart` namespace 被发现。

## function_plot

### 我们做什么

`function_plot` 用于根据数学表达式绘制函数图像。

支持：

- 一元函数曲线图：`line_2d`
- 二元函数三维曲面：`surface_3d`
- 二元函数等高线：`contour_2d`
- 多条一元函数同图对比
- 可选零点检测
- 可选局部极值检测
- SVG/PNG 输出
- 学术风格 profile：
  - `academic_default`
  - `academic_slide`
  - `academic_compact`

主链路：

```text
Tool kwargs
  -> FunctionPlotRequest
  -> FunctionExpressionParser
  -> FunctionSampler
  -> FunctionFeatureAnalyzer
  -> FunctionPlotRenderer
  -> FunctionPlotExporter
  -> FunctionPlotResult
```

表达式解析使用 SymPy，采样使用 NumPy，渲染使用 Matplotlib Agg backend。

### 输入边界

Tool schema 接收：

- `plot_kind`
- `expressions`
- `variables`
- `x_range`
- `y_range`
- `samples`
- `output_formats`
- `style_profile`
- `detect_roots`
- `detect_extrema`
- `title`

tool 层负责把 LLM kwargs 收敛成 `FunctionPlotRequest`，只补 schema 表达不了的跨字段约束，例如：

- `surface_3d` / `contour_2d` 只能有一个表达式。
- 二元图变量固定为 `["x", "y"]`。
- 二元图 `samples <= 250`。
- range 必须 finite 且递增。

parser 层负责真实表达式安全边界：

- 禁止 `eval` / `exec` / `import` / `open` / `lambda`。
- 禁止 `__`。
- 禁止属性访问和下标访问相关字符。
- 只允许白名单函数、常量、变量。
- `parse_expr` 使用受控 `local_dict` / `global_dict`。

### 我们不做什么

`function_plot` 不做：

- 任意 Python 执行。
- 任意 SymPy 对象访问。
- 任意 Matplotlib 代码执行。
- 统计图、表格图、业务图表。
- 可追溯 Note 图表。
- 交互式图表。
- Plotly/Altair/Bokeh 输出。
- 用户自定义 rcParams。
- 证明级数学分析。

零点和极值检测只是绘图辅助标注，不承诺数学证明。

## stat_chart

### 我们做什么

`stat_chart` 用于根据结构化表格数据绘制统计图。

支持的第一版图型：

- `scatter`
- `line`
- `histogram`
- `kde`
- `ecdf`
- `bar`
- `count`
- `box`
- `violin`
- `boxen`
- `strip`
- `swarm`
- `point`
- `regression`
- `residual`
- `heatmap`
- `correlation_heatmap`
- `pairplot`，受限支持

主链路：

```text
Tool kwargs
  -> StatChartRequest
  -> DataFrameBuilder
  -> StatChartSpecValidator
  -> StatChartRenderer
  -> StatChartExporter
  -> StatChartResult
```

数据规整使用 pandas，统计图渲染使用 seaborn axes-level API，最终由 Matplotlib 导出 SVG/PNG。

### 输入边界

第一版只接收 inline records：

```json
{
  "records": [
    {"group": "A", "score": 82},
    {"group": "B", "score": 76}
  ]
}
```

不接收：

- `data_ref`
- `csv_text`
- `file_ref`
- SQL
- 任意 dataframe 操作代码

mapping 字段只允许列名：

- `x`
- `y`
- `value`
- `hue`
- `style`
- `size`
- `row`
- `col`

DataFrame 边界负责：

- records 行数上限。
- columns 数量上限。
- DataFrame 构造。
- 轻量数值类型推断。

Validator 边界负责：

- mapping 字段必须存在于 DataFrame columns。
- mapping 字段必须是普通列名，不允许表达式。
- 按 `chart_kind` 检查必填字段。
- facet 组合数量限制。
- heatmap pivot 尺寸限制。
- pairplot 行数和数值列数量限制。
- correlation heatmap 至少需要两个数值列。

### 我们不做什么

`stat_chart` 不做：

- 用户写 Python。
- LLM 写 seaborn/matplotlib 调用。
- 用户传自由 `seaborn_kwargs` / `matplotlib_kwargs`。
- 任意 pandas groupby/pivot/filter/transform 代码。
- BI dashboard。
- 可追溯图表。
- 数学函数图像。
- 任意文件读取。
- CSV/file_ref/data_ref 输入，第一版只做 inline records。
- 交互式图表。
- Plotly/Altair/Bokeh 输出。
- 用户自定义 palette 或 raw rcParams。

## 前端预览约定

两个新工具都返回 inline artifacts：

```json
{
  "artifacts": [
    {
      "kind": "svg",
      "mime_type": "image/svg+xml",
      "content": "<svg>...</svg>",
      "encoding": "text"
    },
    {
      "kind": "png",
      "mime_type": "image/png",
      "content": "...base64...",
      "encoding": "base64"
    }
  ]
}
```

前端优先展示 SVG artifact；没有 SVG 时展示 PNG base64。

这和旧 `image_file_ref` / `mock_preview_markdown` 方式不同。旧方式需要下载临时文件，新方式适合 tool 结果直接预览。

## 何时选择哪个工具

使用 `function_plot`：

- 用户给数学表达式。
- 用户要求画函数曲线、三维曲面、等高线。
- 用户要求标注函数零点或极值点。

使用 `stat_chart`：

- 用户给表格、records、实验结果、观测数据。
- 用户要求散点图、箱线图、直方图、柱状图、相关矩阵等统计图。
- 用户要求按字段映射 `x/y/hue` 绘图。

不要混用：

- `sin(x)` 这类表达式不要交给 `stat_chart`。
- 表格里的 `score/group/model` 这类统计数据不要交给 `function_plot`。
