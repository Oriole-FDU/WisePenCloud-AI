# 可溯源 Chart Render Tool — 开源库经验蒸馏报告

> 版本：v1.0
> 日期：2026-06-02
> 范围：matplotlib / seaborn / altair / vega-lite / plotly.py

---

## 一、总体结论

经过对 matplotlib、seaborn、altair、vega-lite、plotly.py 五个核心库的深度分析，得出以下关键结论：

1. **声明式规范 + 命令式渲染是行业共识**。Vega-Lite/Altair 用 JSON Spec 描述意图，Plotly 用 Figure 对象描述意图，Seaborn objects 用 Plot+Mark+Stat 描述意图——最终都走向"先声明、后编译/渲染"的分离。

2. **可溯源不是任何库的原生目标**。Altair 的哈希仅用于去重，Plotly 的双层数据模型仅区分用户值与默认值，Vega-Lite 的 usermeta 仅是透传字段。我们必须在架构层面原生植入溯源能力。

3. **全局状态是最大的架构敌人**。matplotlib 的 rcParams、Gcf、fontManager 全局单例，seaborn 的 set_theme() 修改全局 rcParams——都导致多实例渲染不可隔离。我们的工具必须从第一天起做到零全局状态。

4. **统计变换与视觉表示必须正交**。Seaborn 的 Mark/Stat/Move 三元组、Vega-Lite 的 Transform 管道 + Encoding 分离——这是最值得蒸馏的架构模式。

5. **Schema 驱动 + 验证器管线是保证规范正确性的最佳手段**。Altair 和 Plotly 都从 JSON Schema 自动生成类型安全的 Python 类，每个属性都有验证器。

---

## 二、推荐主链路

```
ChartSpec (声明式 JSON 规范)
    ↓ [Validate] — Schema 验证 + 属性验证器
    ↓ [Compile]  — 数据解析 + 统计变换 + 视觉映射解析
    ↓ [Render]   — MatplotlibChartRenderer (纯 OO 接口，零全局状态)
    ↓ [Output]   — SVG / PNG + ChartManifest
```

**主链路只依赖 matplotlib**。其他库的设计经验通过蒸馏转化为我们自己的协议层，不引入运行时依赖。

---

## 三、逐库蒸馏结果

### 3.1 matplotlib/matplotlib

| 维度 | 蒸馏内容 |
|------|---------|
| **最值得蒸馏的设计点** | ① Artist 层次 + Stale 脏标记传播链（渲染树增量更新）<br>② Renderer/Canvas 三层分离（桥接模式，输出格式可替换）<br>③ LayoutEngine 策略模式（布局算法可插拔）<br>④ FontProperties 声明式描述 + FontManager 评分匹配<br>⑤ ColormapRegistry 返回副本（防全局污染）<br>⑥ rcParams 验证器模式（写入时即验证）<br>⑦ Legend 的 Handler 模式（不同 Artist 类型有不同图例绘制器）<br>⑧ `_PropCycle` 条件推进（颜色循环可重现） |
| **不能进入主链路的点** | ① rcParams 全局可变单例<br>② pyplot 接口（plt.gcf/gca/plot）<br>③ Gcf 全局 Figure 管理<br>④ fontManager 全局单例<br>⑤ `_process_plot_var_args` MATLAB 风格可变参数<br>⑥ `_AxesBase` 巨型类（数千行，职责过多） |
| **可转化为我们协议的点** | ① Renderer/Canvas 分离 → ChartRenderer 接口<br>② LayoutEngine 策略 → ChartLayout 可序列化<br>③ FontProperties → ChartTheme.font<br>④ ColormapRegistry → ChartTheme.colormap<br>⑤ rcParams 验证器 → ChartSpec 属性验证器<br>⑥ Legend Handler → ChartPreset 的 legend 策略 |
| **对 Matplotlib backend 的具体启发** | ① 渲染时必须用 OO 接口（Figure/Axes），禁止 pyplot<br>② 每个 render 调用创建独立 Figure，渲染完即销毁，不缓存<br>③ 用 rc_context() 或手动 rcParams 副本隔离配置<br>④ SVG 输出用 backend_svg，PNG 输出用 backend_agg<br>⑤ 字体通过 FontProperties 显式指定，不依赖全局 fontManager 查找 |
| **对 provenance/fingerprint 的影响** | ① rcParams 快照必须纳入 render_fingerprint<br>② matplotlib.__version__ 必须纳入 renderer_version<br>③ LayoutEngine 选择和参数必须记录<br>④ 字体回退结果（实际使用的字体名）必须记录，而非仅记录请求的字体 |

#### matplotlib 核心架构要点

- **Artist 层次**：一切皆 Artist，`draw(renderer)` 是统一渲染接口。Stale 脏标记通过 `stale_callback` 向上冒泡到 Figure → Canvas，驱动重绘。
- **Figure/Axes 分层**：Figure 是顶层容器，Axes 是绘图核心。SubFigure 支持嵌套布局。
- **rcParams**：带验证器的全局可变字典，`rc_context()` 支持临时配置但本质仍是全局状态操作。
- **Backend 三层**：RendererBase（绘图原语）/ FigureCanvasBase（画布管理+输出）/ GraphicsContextBase（绘图状态）。最小接口原则：只需实现 `draw_path` 即可获得可工作的后端。
- **LayoutEngine 策略模式**：TightLayoutEngine / ConstrainedLayoutEngine / PlaceHolderLayoutEngine，通过 `engine.execute(fig)` 执行。
- **FontProperties**：基于 W3C CSS1 规范的声明式字体描述，FontManager 用评分匹配算法选择最佳字体。
- **全局状态问题**：rcParams、Gcf、fontManager 都是全局单例，多线程/多 Figure 场景下存在状态污染风险。

---

### 3.2 mwaskom/seaborn

| 维度 | 蒸馏内容 |
|------|---------|
| **最值得蒸馏的设计点** | ① **Mark/Stat/Move 三元组**——统计变换、视觉表示、位置调整正交组合<br>② **Mappable 属性系统**——每个视觉属性可以是直接值、数据映射、依赖属性、rcParam 引用<br>③ **Property 注册表**——30+ 种视觉属性统一注册，每种关联默认 Scale 类型<br>④ **Scale 管道**——Continuous/Nominal/Temporal/Boolean 四种标度，每种实现 `_pipeline` 可组合变换链<br>⑤ **调色板统一入口**——color_palette() 一个函数通过参数类型分发处理所有调色板需求<br>⑥ **色盲友好内置**——colorblind 调色板是一级公民<br>⑦ **主题三维正交分解**——style/context/palette 独立配置<br>⑧ **上下文管理器 + 白名单**——只修改预定义 rcParams 子集，退出时恢复<br>⑨ **auto/brief/full Legend 策略**——智能控制图例信息密度<br>⑩ **DataFrame 管道**——统计变换输入输出都是 DataFrame，天然可溯源 |
| **不能进入主链路的点** | ① set_theme() 修改全局 rcParams<br>② 两套并行体系（函数式 + 声明式）的概念负担<br>③ FacetGrid/PairGrid 的全局 legend 收集逻辑<br>④ wide-form 自动转换（静默改写数据结构）<br>⑤ 无 CJK 字体处理 |
| **可转化为我们协议的点** | ① Mark/Stat 分离 → ChartPreset = chart_type + stat + move<br>② Mappable 属性 → ChartEncoding 的每个 channel 支持 value/field 两种模式<br>③ Property 注册表 → ChartEncoding 的 channel 类型注册表<br>④ Scale 管道 → ChartEncoding 的 scale 配置<br>⑤ 调色板系统 → ChartTheme.palette<br>⑥ 主题三维分解 → ChartTheme.style + ChartTheme.context + ChartTheme.palette<br>⑦ auto/brief/full → ChartSpec.legend_verbosity |
| **对 Matplotlib backend 的具体启发** | ① despine 默认策略（移除顶部和右侧脊柱）<br>② force_edgecolor 确保小面积图形元素可见<br>③ solid_capstyle="round" 线条端点圆滑<br>④ legend 外置 + 动态扩宽 figure<br>⑤ 统计变换结果直接传给 matplotlib API，无需中间格式 |
| **对 provenance/fingerprint 的影响** | ① 统计变换（estimator, ci, n_boot）必须纳入 spec_fingerprint<br>② 调色板名称 + 参数必须纳入 theme_fingerprint<br>③ wide→long 转换必须记录（如果支持）<br>④ 变量类型推断结果必须记录（numeric/categorical/datetime） |

#### seaborn 核心架构要点

- **两套并行体系**：函数式 API（scatterplot/barplot + VectorPlotter + SemanticMapping）和声明式 API（Plot + Mark + Stat + Move）。
- **Mark/Stat/Move 三元组**：Mark 是视觉表示（@dataclass），Stat 是统计变换（@dataclass + `__call__`），Move 是位置调整（Dodge/Stack/Jitter/Shift/Norm）。三者正交组合。
- **Mappable 属性系统**：每个 Mark 的视觉属性都可以是直接值、数据映射、依赖属性、rcParam 引用。`_resolve()` 按优先级解析：直接指定 > 数据映射 > 依赖属性 > 默认值。
- **调色板系统**：`color_palette()` 统一入口，支持 seaborn 命名/HLS/HUSL/Cubehelix/light/dark/blend/matplotlib colormap/list/dict。色盲友好调色板内置。
- **主题三维正交**：style（darkgrid/whitegrid/dark/white/ticks）× context（paper/notebook/talk/poster）× palette（deep/muted/bright/pastel/dark/colorblind）。
- **无 CJK 字体处理**：字体配置完全委托 matplotlib，默认字体列表无 CJK 字体。

---

### 3.3 vega/altair

| 维度 | 蒸馏内容 |
|------|---------|
| **最值得蒸馏的设计点** | ① **Schema 驱动 + 自动生成**——Vega-Lite JSON Schema 是 single source of truth，Python 类自动生成<br>② **SchemaBase 统一基类**——所有规范元素继承同一基类，携带 `_schema` 元数据<br>③ **Shorthand 语法**——`"price:Q"` 解析为 `{field, type}`，降低使用门槛<br>④ **Encoding 三态**——Field（字段引用）/Value（常量值）/Datum（单值引用）三种 channel 引用模式<br>⑤ **Data Transformer 管线**——可插拔的数据转换器（default/json/csv/vegafusion）<br>⑥ **PluginRegistry**——主题/渲染器/数据转换器统一注册机制，支持 entry_points<br>⑦ **数据集哈希**——SHA-256 截断生成确定性名称，自动去重<br>⑧ **方法链 + copy-on-write**——每次操作返回新对象，原始 spec 不被修改<br>⑨ **Schema 版本绑定**——SCHEMA_VERSION 硬编码，确保可精确复现<br>⑩ **友好的验证错误**——按 JSON 路径分组、去重、生成修复建议 |
| **不能进入主链路的点** | ① 自动生成的 core.py 超大（1.5MB+）<br>② 条件 encoding 的类型系统极其复杂<br>③ consolidate_datasets 全局开关可能导致意外行为<br>④ MaxRowsError 5000 行限制<br>⑤ 不保证跨版本复现 |
| **可转化为我们协议的点** | ① Schema 驱动 → ChartSpec 用 JSON Schema 定义权威规范<br>② SchemaBase → 所有规范元素继承 TraceableSchemaBase<br>③ Shorthand → ChartEncoding 支持 shorthand 语法<br>④ Encoding 三态 → ChartEncoding.channel 支持 field/value 两种模式<br>⑤ Data Transformer → ChartDataRef 的数据转换管线<br>⑥ PluginRegistry → 主题/预设的注册机制<br>⑦ 数据集哈希 → data_fingerprint<br>⑧ copy-on-write → ChartSpec 不可变<br>⑨ Schema 版本 → spec_version 字段<br>⑩ 友好验证 → ChartSpec 验证错误包含上下文 |
| **对 Matplotlib backend 的具体启发** | ① 编译阶段与渲染阶段分离——Altair 编译为 Vega，我们编译为 matplotlib 指令<br>② 规范化（Normalize）阶段——将用户友好的简写展开为完整规范 |
| **对 provenance/fingerprint 的影响** | ① spec_version 必须纳入 spec_fingerprint<br>② 数据集哈希 = data_fingerprint<br>③ 主题名称 + 合并顺序必须记录<br>④ copy-on-write 天然支持变更链追踪 |

#### Altair 核心架构要点

- **Schema 驱动**：vega-lite-schema.json 是 Vega-Lite v6.4.1 的完整规范，tools/generate_schema_wrapper.py 自动生成 Python 类。
- **SchemaBase 基类**：`_args` + `_kwds` 双通道存储，`__setattr__` 重写为参数设置，`to_dict()` 递归序列化 + jsonschema 验证。Undefined 单例区分"未设置"和"设为 None"。
- **Encoding 系统**：FieldChannelMixin / ValueChannelMixin / DatumChannelMixin 三种 Mixin。Shorthand `"price:Q"` 通过 `parse_shorthand()` 解析。
- **数据引用**：InlineData / UrlData / NamedData 三种数据源。DataTransformerRegistry 支持可插拔转换器。`_dataset_name()` 用 SHA-256 哈希生成确定性名称。
- **主题系统**：ThemeRegistry 基于 PluginRegistry，支持装饰器注册、上下文管理器切换、entry_points 自动发现。
- **哈希机制**：数据集哈希（SHA-256 截断 32 字符）、参数哈希（截断 16 字符）、图表哈希（截断 16 字符）。

---

### 3.4 vega/vega-lite

| 维度 | 蒸馏内容 |
|------|---------|
| **最值得蒸馏的设计点** | ① **Spec 泛型参数化**——区分"用户友好"的外部规范和"编译器友好"的内部规范<br>② **5 阶段编译管线**——Normalize → Build Model → Parse → Optimize → Assemble<br>③ **Channel 分类体系**——Position/Polar/Geo/Offset/MarkProperty/NonScale/Facet 七大类<br>④ **Channel 与 Mark 兼容性矩阵**——supportMark() 返回 always/binned/undefined<br>⑤ **ChannelDef 类型系统**——FieldDef/DatumDef/ValueDef/ConditionalDef 四态<br>⑥ **Config 优先级链**——Encoding > MarkDef > 专用Config > 通用Config > Default<br>⑦ **Resolve 系统**——shared/independent 二元策略控制组合视图中 scale/axis/legend 的共享<br>⑧ **Transform 从 Encoding 中提取**——extractTransformsFromEncoding() 使 encoding 只关心视觉映射<br>⑨ **usermeta 字段**——携带自定义元数据而不影响编译<br>⑩ **Component 中间表示**——Parse 和 Assemble 之间的中间层，支持合并和优化 |
| **不能进入主链路的点** | ① 类型层级过深（7-8 层 ChannelDef）<br>② 大量 `as any` 绕过类型检查<br>③ Config 层次过细（20+ 种 Axis 交叉组合）<br>④ 编译器与规范耦合过重 |
| **可转化为我们协议的点** | ① Spec 泛型参数化 → 区分 UserChartSpec 和 ResolvedChartSpec<br>② 编译管线 → validate → normalize → compile → render<br>③ Channel 分类 → ChartEncoding 的 channel 类型定义<br>④ 兼容性矩阵 → chart_type 与 encoding channel 的校验规则<br>⑤ Config 优先级链 → ChartSpec > ChartPreset > ChartTheme > Default<br>⑥ Resolve → 组合图表的 scale/axis 共享策略<br>⑦ usermeta → ChartManifest 的溯源元数据<br>⑧ Component 中间表示 → 编译阶段的中间数据结构 |
| **对 Matplotlib backend 的具体启发** | ① 编译管线思想——先解析规范，再生成 matplotlib 指令<br>② 自底向上合并——子图表先编译，父图表合并子组件 |
| **对 provenance/fingerprint 的影响** | ① Normalize 前后的 spec 都应保留（可对比）<br>② Transform 管线的每步输入/输出 schema 应记录<br>③ Config 优先级链的每层来源应记录<br>④ usermeta 可存储 AI 生成信息 |

#### Vega-Lite 核心架构要点

- **5 阶段编译管线**：Normalize（展开复合标记、快捷语法）→ Build Model（递归实例化 Model 子类）→ Parse（自底向上解析 data/scale/axis/legend/mark）→ Optimize（合并/去重数据源）→ Assemble（转换为 Vega Spec）。
- **Spec 体系**：UnitSpec / LayerSpec / FacetSpec / ConcatSpec / RepeatSpec / TopLevel，通过泛型参数化区分外部规范和内部规范。Mixin 组合（BaseSpec + DataMixins + LayoutSizeMixins + ResolveMixins）。
- **Channel 分类**：Position（x/y/x2/y2）、Polar（theta/radius）、Geo（lat/lon）、Offset（xOffset/yOffset）、MarkProperty（color/fill/stroke/opacity/size/angle/shape）、NonScale（text/order/detail/tooltip）、Facet（row/column）。
- **ChannelDef 四态**：FieldDef（字段引用）/ DatumDef（单值引用）/ ValueDef（常量值）/ ConditionalDef（条件）。
- **Config 优先级链**：Encoding 值 > MarkDef 属性 > Mark 专用 Config > 通用 Mark Config > Style Config > 默认值。
- **Resolve 系统**：scale/axis/legend 各自支持 shared/independent 二元策略。

---

### 3.5 plotly/plotly.py

| 维度 | 蒸馏内容 |
|------|---------|
| **最值得蒸馏的设计点** | ① **Schema-Driven Code Gen**——从 plot-schema.json 自动生成所有 graph_objects 类<br>② **Validator Pipeline**——每个属性都有验证器，验证+强制转换一体<br>③ **双层数据模型**——_data（用户值）+ _data_defaults（前端默认值）分离<br>④ **Template 系统**——Lazy Loading + `+` 号组合合并<br>⑤ **Express 声明式 API**——DataFrame + 列名映射 → 自动推断 trace/颜色/轴<br>⑥ **动态子图属性**——xaxis2/yaxis3 等动态编号属性<br>⑦ **Narwhals DataFrame 抽象**——支持 pandas/Polars/PyArrow 多后端<br>⑧ **路径式属性访问**——`fig["layout.xaxis.range"]` + 下划线简写<br>⑨ **matplotlylib 访问者模式**——Exporter 爬取 + Renderer 转换<br>⑩ **安全 JSON 序列化**——转义 `<>/` 防 XSS |
| **不能进入主链路的点** | ① 代码生成导致可读性差（自动生成类数千行）<br>② BasePlotlyType 过度复杂（6000+ 行）<br>③ Express 与 go 的割裂（两套 API）<br>④ Kaleido 依赖 Chrome headless<br>⑤ matplotlylib 不完整（大量图元不支持） |
| **可转化为我们协议的点** | ① Schema-Driven → ChartSpec 用 JSON Schema 定义，可自动生成验证器<br>② Validator Pipeline → ChartSpec 每个字段的验证器<br>③ 双层数据模型 → ChartSpec 区分用户显式值和默认值<br>④ Template 系统 → ChartTheme 的 Lazy Loading + 组合合并<br>⑤ Express API → 高级声明式 API（如果未来需要）<br>⑥ 路径式属性访问 → ChartSpec 的嵌套属性操作<br>⑦ 安全序列化 → 输出 JSON 时的安全处理 |
| **对 Matplotlib backend 的具体启发** | ① matplotlylib 的 Exporter/Renderer 访问者模式——如果需要从其他格式转换到我们的规范<br>② Template 的 trace 默认样式——可以定义每种 chart_type 的默认 matplotlib 样式 |
| **对 provenance/fingerprint 的影响** | ① 双层数据模型可扩展为三层：用户值 + 默认值 + 值来源<br>② Template 名称 + 合并顺序必须记录<br>③ Validator 的强制转换结果必须记录（原始值 vs 转换后值）<br>④ Kaleido 版本（如果用）必须纳入 renderer_version |

#### Plotly.py 核心架构要点

- **三层架构**：Plotly Express（高级声明式 API）→ Graph Objects（中级面向对象 API）→ BaseDatatypes + Validator System + Codegen（底层类型系统）。
- **BaseFigure 双层数据模型**：`_data`（用户显式值）与 `_data_defaults`（前端默认值）分离。Reparenting 机制确保 Figure 与子对象数据一致性。
- **Trace 系统**：40+ 种自动生成的 trace 类型，属性代理 + 验证器模式。复合属性树（Scatter.marker.line.width 等深度嵌套）。
- **Layout 动态子图属性**：xaxis2/yaxis3 等通过正则匹配动态创建，支持 make_subplots 多子图。
- **Template 系统**：TemplatesConfig 单例，Lazy Loading 内置模板，`+` 号组合合并（LCM 扩展 + 右覆盖左）。
- **Express 数据绑定**：direct_attrables / array_attrables / group_attrables / renameable_group_attrables 四种属性类别。Mapping 命名元组是核心数据绑定抽象。
- **Validator 层次**：BaseValidator → DataArrayValidator / EnumeratedValidator / BooleanValidator / NumberValidator / ColorValidator / CompoundValidator 等。ValidatorCache 按需创建并缓存。
- **matplotlylib**：Exporter（爬取 matplotlib Figure）+ PlotlyRenderer（转换为 Plotly trace/layout），访问者模式。

---

## 四、v1 ChartSpec 草案

```python
class ChartSpec:
    spec_version: str                          # "1.0.0"
    chart_id: str                              # 确定性哈希，由内容生成
    chart_type: str                            # "bar" | "line" | "scatter" | ...

    data: ChartDataRef                         # 数据来源
    encoding: ChartEncoding                    # 视觉映射
    mark: Optional[ChartMark]                  # 标记配置

    title: Optional[ChartText]                 # 图表标题
    subtitle: Optional[ChartText]              # 副标题
    caption: Optional[ChartText]               # 图表说明

    width: Optional[int]                       # 画布宽度 (px)
    height: Optional[int]                      # 画布高度 (px)
    dpi: Optional[int]                         # 输出 DPI

    theme_id: Optional[str]                    # 引用的主题 ID
    preset_id: Optional[str]                   # 引用的预设 ID

    transforms: Optional[List[ChartTransform]] # 数据变换管道

    usermeta: Optional[Dict[str, Any]]         # 用户自定义元数据（不影响编译）
```

**关键设计决策**：

- **chart_id 由确定性哈希生成**：对 (spec_version, chart_type, data.fingerprint, encoding, mark, title, transforms) 的规范化 JSON 做 SHA-256，截断 16 字符。不包含 theme_id/preset_id（它们通过 manifest 追踪）。
- **ChartText 携带 derivation_kind**：区分 `"manual"`（用户手写）、`"data_derived"`（从数据生成）、`"model_generated"`（AI 生成）。
- **transforms 是显式管道**：每个 transform 记录类型、参数、输入字段、输出字段，支持数据血缘追踪。

---

## 五、v1 ChartDataRef 草案

```python
class ChartDataRef:
    source_type: str                           # "inline" | "url" | "ref"
    source_ref: Optional[str]                  # 外部数据源标识（数据库表名/文件路径/URL）
    values: Optional[List[Dict[str, Any]]]     # inline 数据
    data_fingerprint: str                      # 数据内容的 SHA-256 截断
    schema_hash: Optional[str]                 # 数据 schema 的哈希（列名+类型）
    format_hint: Optional[str]                 # "csv" | "json" | "parquet"
```

**关键设计决策**：

- **三种数据源**：inline（内联数据）、url（远程引用）、ref（平台内部引用，如数据库表名）。
- **data_fingerprint 是必填字段**：对 values 做规范化 JSON 序列化后 SHA-256 截断 32 字符。url/ref 模式下由调用方提供。
- **schema_hash 可选**：记录列名和类型的哈希，用于检测 schema 变更。
- **数据不符合 schema 时直接报错，不做宽容转换**。

---

## 六、v1 ChartEncoding 草案

```python
class ChartEncoding:
    x: Optional[ChartChannel]
    y: Optional[ChartChannel]
    x2: Optional[ChartChannel]                 # 范围图的第二端点
    y2: Optional[ChartChannel]
    color: Optional[ChartChannel]
    size: Optional[ChartChannel]
    shape: Optional[ChartChannel]              # v1 后置
    opacity: Optional[ChartChannel]            # v1 后置
    label: Optional[ChartChannel]              # 数据标签
    tooltip: Optional[ChartChannel]            # v1 后置
    detail: Optional[ChartChannel]             # v1 后置
    row: Optional[ChartChannel]                # facet 行（v1 后置）
    column: Optional[ChartChannel]             # facet 列（v1 后置）


class ChartChannel:
    field: Optional[str]                       # 字段名（field 模式）
    value: Optional[Any]                       # 常量值（value 模式）
    type: Optional[str]                        # "quantitative" | "nominal" | "temporal"
    aggregate: Optional[str]                   # "sum" | "mean" | "count" | "min" | "max" | None
    bin: Optional[Union[bool, int]]            # 是否分箱 / 分箱数
    title: Optional[str]                       # 轴/图例标题（覆盖字段名）
    scale: Optional[ChartScale]                # 标度配置
    sort: Optional[str]                        # "ascending" | "descending" | None


class ChartScale:
    type: Optional[str]                        # "linear" | "log" | "ordinal" | "band" | None
    domain: Optional[List[Any]]                # 值域
    range: Optional[List[Any]]                 # 视觉范围
    reverse: Optional[bool]
    clamp: Optional[bool]                      # v1 后置
    zero: Optional[bool]                       # 是否包含零点
```

**关键设计决策**：

- **field/value 二态**：每个 channel 只能是 field 模式（从数据映射）或 value 模式（常量值），不允许同时设置。这是从 Altair 的 Field/Value/Datum 三态简化而来。
- **type 是必填的（field 模式下）**：不做静默推断。如果用户不提供 type，在 validate 阶段报错。这保证了溯源的确定性。
- **aggregate 和 bin 是 encoding 层面的**：不隐式提取为 transform。如果需要 transform 管道，显式写在 ChartSpec.transforms 中。
- **sort 只支持 ascending/descending**：不支持自定义排序数组（v1 后置）。

---

## 七、v1 ChartTheme 草案

```python
class ChartTheme:
    theme_id: str                              # 主题唯一标识
    theme_version: str                         # 主题版本号

    font: ChartFontConfig
    color: ChartColorConfig
    axis: ChartAxisConfig
    grid: ChartGridConfig
    legend: ChartLegendConfig
    caption: ChartCaptionConfig
    spacing: ChartSpacingConfig


class ChartFontConfig:
    family: str                                # 主字体族
    cjk_family: Optional[str]                  # CJK 回退字体族
    size_base: int                             # 基础字号 (pt)
    title_size: Optional[int]                  # 标题字号（默认 size_base * 1.4）
    axis_label_size: Optional[int]             # 轴标签字号
    tick_label_size: Optional[int]             # 刻度标签字号
    caption_size: Optional[int]                # 说明文字字号
    legend_size: Optional[int]                 # 图例字号
    weight_title: Optional[str]                # 标题字重


class ChartColorConfig:
    palette_categorical: List[str]             # 分类调色板
    palette_sequential: Optional[str]          # 连续调色板名称
    palette_diverging: Optional[str]           # 发散调色板名称
    background: str                            # 图表背景色
    foreground: str                            # 前景色（文字等）
    axis_line_color: Optional[str]             # 轴线颜色
    grid_color: Optional[str]                  # 网格线颜色


class ChartAxisConfig:
    show: bool                                 # 是否显示轴
    label_angle: Optional[int]                 # 标签旋转角度
    label_format: Optional[str]                # 数字/日期格式
    tick_count: Optional[int]                  # 刻度数量提示
    spine_visible: Dict[str, bool]             # {"left": True, "right": False, "top": False, "bottom": True}


class ChartGridConfig:
    show: bool
    axis: str                                  # "x" | "y" | "both"
    style: str                                 # "solid" | "dashed" | "dotted"
    alpha: float                               # 透明度


class ChartLegendConfig:
    show: bool
    position: str                              # "right" | "bottom" | "inside" | "none"
    verbosity: str                             # "auto" | "brief" | "full"
    frame_on: bool                             # 是否显示图例边框


class ChartCaptionConfig:
    show: bool
    position: str                              # "below" | "above"
    align: str                                 # "left" | "center" | "right"


class ChartSpacingConfig:
    figure_margin: Dict[str, float]            # {"left": 0.1, "right": 0.05, "top": 0.1, "bottom": 0.1}
    subplots_hspace: Optional[float]           # 子图水平间距（v1 后置）
    subplots_wspace: Optional[float]           # 子图垂直间距（v1 后置）
```

**关键设计决策**：

- **CJK 字体是一级公民**：ChartFontConfig.cjk_family 专门处理中文字体回退。渲染时优先使用 cjk_family，回退到 family。
- **despine 默认开启**：spine_visible 默认 `{"left": True, "right": False, "top": False, "bottom": True}`，蒸馏自 seaborn。
- **Legend verbosity 蒸馏自 seaborn**：auto/brief/full 三级控制图例信息密度。
- **主题是不可变的**：ChartTheme 一旦创建不可修改。需要定制时创建新实例或使用 with_override()。

---

## 八、v1 ChartPreset 草案

```python
class ChartPreset:
    preset_id: str                             # 预设唯一标识
    chart_type: str                            # 对应的 chart_type
    description: str                           # 预设描述

    mark_defaults: Dict[str, Any]              # 标记默认属性
    encoding_defaults: Dict[str, Any]          # 编码默认属性
    stat: Optional[str]                        # 统计变换: "identity" | "mean" | "sum" | "count" | ...
    move: Optional[str]                        # 位置调整: "dodge" | "stack" | "fill" | None
    required_channels: List[str]               # 必须的 channel: ["x", "y"]
    optional_channels: List[str]               # 可选的 channel: ["color", "size", "label"]
```

### v1 预设定义

| preset_id | chart_type | stat | move | required_channels | optional_channels | mark_defaults |
|-----------|-----------|------|------|-------------------|-------------------|---------------|
| `bar` | bar | identity | dodge | x, y | color, label | width=0.8, edgecolor=auto |
| `line` | line | identity | None | x, y | color, size | linewidth=2, marker=none |
| `scatter` | scatter | identity | None | x, y | color, size, label | s=50, alpha=0.7 |
| `histogram` | histogram | count | stack | x | color | edgecolor=auto |
| `heatmap` | heatmap | identity | None | x, y, color | — | — |
| `pie` | pie | identity | None | color | label | — |
| `table` | table | identity | None | — | — | — |

**关键设计决策**：

- **stat + move 蒸馏自 seaborn**：统计变换和位置调整正交组合。bar 默认 dodge，histogram 默认 stack。
- **required/optional channels 蒸馏自 vega-lite**：校验时检查必须 channel 是否存在。
- **mark_defaults 和 encoding_defaults 是"低优先级默认值"**：ChartSpec 中的显式值优先级高于 preset 默认值。优先级链：ChartSpec > ChartPreset > ChartTheme > Hardcoded Default。

---

## 九、v1 ChartManifest 草案

```python
class ChartManifest:
    chart_id: str                              # 对应 ChartSpec.chart_id
    spec_fingerprint: str                      # ChartSpec 规范的确定性哈希
    data_fingerprint: str                      # 数据内容的确定性哈希
    render_fingerprint: str                    # 渲染结果的确定性哈希

    spec_version: str                          # ChartSpec 的 spec_version
    renderer_version: str                      # 渲染器版本 (含 matplotlib 版本)
    theme_id: Optional[str]                    # 使用的主题 ID
    theme_fingerprint: Optional[str]           # 主题内容的确定性哈希
    preset_id: Optional[str]                   # 使用的预设 ID

    output_format: str                         # "svg" | "png"
    output_size: Optional[Tuple[int, int]]     # (width_px, height_px)
    output_hash: Optional[str]                 # 输出文件的 SHA-256

    render_timestamp: str                      # ISO 8601 渲染时间
    render_duration_ms: Optional[int]          # 渲染耗时

    font_resolved: Optional[Dict[str, str]]    # 实际解析到的字体
    warnings: Optional[List[str]]              # 渲染过程中的警告

    derivation_log: Optional[List[DerivationEntry]]  # 文本溯源日志


class DerivationEntry:
    element: str                               # "title" | "caption" | "axis_label.x" | "legend_label.color" | ...
    derivation_kind: str                       # "manual" | "data_derived" | "model_generated" | "default"
    source_ref: Optional[str]                  # 来源引用（字段名/模型名/规则名）
    original_value: Optional[str]              # 原始值（如果被转换过）
```

**关键设计决策**：

- **三个指纹分层**：spec_fingerprint（规范不变性）、data_fingerprint（数据不变性）、render_fingerprint（渲染可复现性）。
- **font_resolved 记录实际字体**：而非请求字体。这是 matplotlib 字体回退的溯源关键。
- **derivation_log 记录文本溯源**：每个文本元素（标题、标注、轴标签、图例标签）如果来自数据或模型生成，必须记录 derivation_kind。
- **warnings 不静默丢弃**：渲染过程中的所有警告都记录在 manifest 中。

---

## 十、render_fingerprint 组成字段

render_fingerprint 由以下字段的规范化 JSON 做 SHA-256 截断 32 字符：

| 序号 | 字段 | 说明 |
|------|------|------|
| 1 | spec_fingerprint | 规范指纹 |
| 2 | data_fingerprint | 数据指纹 |
| 3 | renderer_version | 含 matplotlib.\_\_version\_\_ |
| 4 | theme_id + theme_fingerprint | 主题标识 + 内容指纹 |
| 5 | preset_id | 预设标识 |
| 6 | output_format | 输出格式 |
| 7 | dpi | 输出 DPI |
| 8 | width, height | 画布尺寸 |
| 9 | font_resolved | 实际使用的字体映射 |

**不包含**：render_timestamp（不可复现）、render_duration_ms（不可复现）、output_hash（是结果而非输入）。

**稳定性保证**：只要以上 9 个字段不变，render_fingerprint 不变，渲染结果应可复现（bit-exact 除外，视觉一致即可）。

---

## 十一、必须禁止由 renderer 静默推断或修正的字段

| 字段 | 原因 |
|------|------|
| **encoding.channel.type** | 类型推断可能因数据变化而不同，必须显式声明 |
| **encoding.channel.aggregate** | 聚合方式影响数据语义，必须显式声明 |
| **encoding.channel.bin** | 分箱参数影响数据粒度，必须显式声明 |
| **encoding.channel.sort** | 排序方式影响数据呈现，必须显式声明 |
| **data_fingerprint** | 必须由调用方提供或由确定性算法计算，renderer 不应修改数据 |
| **chart_type** | 类型推断可能导致完全不同的图表语义 |
| **title / caption 文本** | 文本内容必须显式提供，renderer 不生成文本 |
| **axis label 文本** | 默认使用字段名，但 renderer 不做"智能"改写 |
| **legend label 文本** | 同上 |
| **color palette 选择** | 必须由 theme 或 spec 显式指定，renderer 不自动选择 |
| **figure size** | 必须由 spec 显式指定，renderer 不自动推断 |
| **数据截断/丢弃** | 绝对禁止。数据超出轴范围时必须报错或由 spec 显式配置 clip |

---

## 十二、MatplotlibRenderer 职责边界

### 职责

- 接收规范化后的 ResolvedChartSpec
- 将其渲染为 SVG/PNG
- 输出 ChartRenderResult（含 ChartManifest）

### 不负责

- 理解自然语言
- 读取业务数据库
- 修改数据语义
- 推断缺失的 encoding 字段
- 选择 chart_type
- 生成 title/caption 文本

### 核心设计约束

1. **每个 render 调用创建独立 Figure**：渲染完即 `plt.close(fig)`，不缓存，不污染后续渲染。
2. **用 rc_context() 隔离主题**：不修改全局 rcParams，主题配置通过 `matplotlib.rc_context()` 临时应用。
3. **字体处理**：通过 FontProperties 显式指定字体，不依赖全局 fontManager 查找。记录实际解析到的字体名到 manifest.font_resolved。
4. **中文处理**：自动检测 CJK 字符，使用 theme.font.cjk_family。设置 `axes.unicode_minus = False`。
5. **bbox_inches="tight"**：确保标题/标注不被裁剪。
6. **legend 策略**：根据 theme.legend.position 决定图例位置，使用 `ax.legend()` 或 `fig.legend()`。
7. **caption 渲染**：使用 `fig.text()` 在 figure 底部添加说明文字。
8. **data label 渲染**：如果 encoding.label 存在，在数据点/柱体上添加文本标注。

### 渲染分发

```python
dispatch = {
    "bar": self._render_bar,
    "line": self._render_line,
    "scatter": self._render_scatter,
    "histogram": self._render_histogram,
    "heatmap": self._render_heatmap,
    "pie": self._render_pie,
    "table": self._render_table,
}
```

---

## 十三、v1 支持范围

### 支持的 chart_type

| chart_type | 最小字段 | 禁止行为 |
|-----------|---------|---------|
| **bar** | x, y | 禁止自动堆叠（必须显式 move="stack"）；禁止负值柱状图静默翻转 |
| **line** | x, y | 禁止自动排序 x 值（必须保持原始顺序）；禁止自动连接缺失值 |
| **scatter** | x, y | 禁止自动回归线；禁止自动聚类 |
| **histogram** | x | 禁止自动选择 bin 数（必须显式 bin=True 或 bin=N）；禁止归一化（必须显式 stat） |
| **heatmap** | x, y, color | 禁止自动插值；禁止静默填充缺失格子 |
| **pie** | color + (y 或 size) | 禁止自动排序扇区；禁止静默合并小扇区 |
| **table** | data | 禁止自动截断行/列；禁止静默格式化数值 |

### 后置的复杂图表

- boxplot / violin plot
- error bar / confidence interval
- stacked area / streamgraph
- radar / polar chart
- treemap / sunburst
- sankey / funnel
- geographic map (choropleth)
- network / graph
- waterfall
- bullet chart

### 后置的 renderer 后端

- SVG 动画 / 交互式 HTML
- Canvas 2D（前端渲染）
- WebAssembly 渲染器
- LaTeX/TikZ 输出

### v1 可实现的美观性 preset

| preset_id | 风格 | 蒸馏来源 |
|-----------|------|---------|
| `"default"` | 白底、despine、浅灰网格、deep 调色板 | seaborn darkgrid |
| `"minimal"` | 白底、无网格、无脊柱 | seaborn ticks |
| `"dark"` | 深色背景、浅色文字 | plotly_dark |
| `"presentation"` | 大字号、高对比度 | seaborn poster context |
| `"paper"` | 小字号、紧凑布局 | seaborn paper context |
| `"cjk_notebook"` | 中文字体优化、适合 Jupyter | 自研 |

---

## 十四、v1 实现顺序

```
Phase 1: 核心协议
  ├── ChartSpec 数据类定义 + JSON Schema
  ├── ChartDataRef 数据类定义
  ├── ChartEncoding 数据类定义
  ├── ChartTheme 数据类定义
  ├── ChartPreset 数据类定义（7 种 chart_type）
  ├── ChartManifest 数据类定义
  └── fingerprint 计算工具函数

Phase 2: 验证层
  ├── ChartSpec JSON Schema 验证
  ├── encoding channel 与 chart_type 兼容性校验
  ├── required_channels 校验
  └── 数据 schema 校验

Phase 3: 编译层
  ├── normalize: 展开 shorthand、填充默认值
  ├── resolve: 合并 ChartSpec > ChartPreset > ChartTheme > Default 优先级链
  └── compile: 生成 ResolvedChartSpec

Phase 4: 渲染层
  ├── MatplotlibChartRenderer 核心框架
  ├── bar / line / scatter 渲染
  ├── histogram / heatmap / pie 渲染
  ├── table 渲染
  ├── 主题应用（rc_context 隔离）
  ├── 字体处理（CJK 检测 + 回退）
  ├── legend 渲染
  ├── title / caption / data label 渲染
  └── SVG / PNG 导出

Phase 5: 溯源层
  ├── ChartManifest 生成
  ├── fingerprint 计算
  ├── derivation_log 记录
  └── font_resolved 记录

Phase 6: 高级 API（可选）
  ├── 声明式构造器（类似 Plotly Express）
  └── from_dataframe() 便捷方法
```

---

## 十五、明确禁止事项

1. **禁止引入 Seaborn/Altair/Vega-Lite/Plotly/ECharts/pyecharts/plotnine/Bokeh/HoloViews 作为运行时依赖**
2. **禁止使用 pyplot 接口**（plt.plot, plt.gcf, plt.gca 等）
3. **禁止修改全局 rcParams**（必须用 rc_context 隔离）
4. **禁止静默推断 encoding.channel.type**（必须显式声明）
5. **禁止静默推断 chart_type**（必须显式声明）
6. **禁止静默截断/丢弃/改写数据**（数据不符合 schema 时直接报错）
7. **禁止 renderer 生成 title/caption 文本**（文本必须由调用方提供）
8. **禁止让 LLM 直接输出 Matplotlib 代码**（LLM 输出 ChartSpec JSON）
9. **禁止缓存 Figure/Axes 对象跨渲染调用**（每次 render 创建新 Figure）
10. **禁止使用 dict[str, str] / list[str] / X | None 语法**（使用 Dict, List, Optional）

---

## 十六、后续可选增强

| 增强项 | 优先级 | 蒸馏来源 |
|--------|--------|---------|
| 组合图表（layer/hconcat/vconcat） | 高 | Vega-Lite view composition |
| Facet 分面 | 高 | Seaborn FacetGrid |
| Transform 管道（aggregate/bin/filter/sort） | 高 | Vega-Lite Transform |
| 条件 encoding（when/then/otherwise） | 中 | Altair ConditionalDef |
| 自定义主题注册（entry_points） | 中 | Altair PluginRegistry |
| Shorthand 语法（"price:Q"） | 中 | Altair parse_shorthand |
| Schema 驱动的类型自动生成 | 中 | Plotly/Altair codegen |
| DataFrame 抽象层（Narwhals） | 低 | Plotly Express |
| 交互式 HTML 输出 | 低 | Plotly Renderer |
| 从 matplotlib Figure 反向提取 ChartSpec | 低 | Plotly matplotlylib |
| 图表 diff 工具 | 低 | 自研 |
| 图表版本管理 | 低 | 自研 |

---

## 附录：蒸馏来源对照表

| 我们的概念 | 主要蒸馏来源 | 次要蒸馏来源 |
|-----------|------------|------------|
| ChartSpec | Altair SchemaBase + Vega-Lite Spec | Plotly BaseFigure |
| ChartDataRef | Altair Data Transformer + Vega-Lite Data | Plotly Express data binding |
| ChartEncoding | Vega-Lite Encoding + Altair ChannelDef | Seaborn Property + Scale |
| ChartTheme | Seaborn 三维正交 + Matplotlib rcParams | Plotly Template |
| ChartPreset | Seaborn Mark/Stat/Move | Vega-Lite Mark + Config |
| ChartManifest | 自研（无直接来源） | Altair 哈希 + Plotly 双层数据模型 |
| render_fingerprint | Altair _compute_hash | Plotly _data/_data_defaults |
| MatplotlibRenderer | Matplotlib OO 接口 + Backend 分离 | Seaborn despine/美观性策略 |
| derivation_log | 自研 | Vega-Lite usermeta |
| font_resolved | Matplotlib FontManager | 自研 |
