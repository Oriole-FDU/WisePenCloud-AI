# XLSX v2 converter

这个目录承载 XLSX 解析路径。入口在 `parse_xlsx.py`：

- `parse_xlsx()` 直接调用 MinerU Office 的 `office_xlsx_analyze()` 和 `union_make()`，
  完整保留 MinerU 输出的 HTML block。
- `fast_parse_xlsx()` 使用本目录的 `XlsxConverter`，直接从 workbook 结构输出
  Markdown/HTML。

## 当前处理逻辑

- fast 路径直接使用 `openpyxl` 读取 workbook，不调用 MinerU 的 `office_xlsx_analyze`
  和 `union_make`。
- 每个可见 sheet 作为一个物理页输出项目 page marker：`<!-- page N -->`。
- 多个非空 sheet 时，在每页开头输出 sheet title；单个非空 sheet 不额外加标题。
- 用工作表中的非空单元格和合并单元格区域做连通分量，识别线性表格区域。
- 普通表格直接渲染 Markdown pipe table。
- 含 `rowspan` / `colspan` 的合并表格直接渲染 HTML table，不经过
  `markdownify` 反解析。
- 单格内容渲染为普通段落。
- `IMAGE("https://...")` 公式渲染为 Markdown image。
- openpyxl 可读取到 anchor 的浮动图片，在传入 `image_path` 时落盘并按 sheet
  坐标插入为独立 Markdown image。

## 蒸馏自 MinerU 的算法

- 可见 sheet 过滤。
- 只有多个非空 sheet 时才输出 sheet 标题。
- 合并单元格 lookup：识别隐藏格，并在左上角保留 `rowspan` / `colspan`。
- 表格区域发现：从有内容或合并关系的单元格出发，按上下左右连通关系聚合表格。
- 表格区域内部保留空单元格，维持矩形表格结构。
- 浮动图片按 anchor 坐标参与 sheet 内排序。

## 有意去掉的重逻辑

- fast 路径不生成 MinerU middle json。
- 不先把表格转 HTML，再用 BeautifulSoup / markdownify 反解析 Markdown。
- parse 路径不修改 `span["html"]`，避免破坏原始 HTML block 属性。
- 不复刻 MinerU 的 chart source table、WPS `DISPIMG`、公式 OMML、富文本 HTML
  样式、复杂图片占位等完整 Office 框架。
- 不做 OCR，不从图片中识别表格内容。
- 不尝试还原图片覆盖单元格的视觉布局。

## 第一版图片边界

支持：

- `IMAGE()` 公式中的 http/https 图片 URL。
- openpyxl 暴露在 `worksheet._images` 且有 anchor 的浮动图片。

暂不支持：

- Excel 新版真实 in-cell embedded picture 的完整 OOXML 解析。
- chart 渲染成图片。
- shape、icon、comment、textbox 内图片。
- WPS 私有图片协议。
