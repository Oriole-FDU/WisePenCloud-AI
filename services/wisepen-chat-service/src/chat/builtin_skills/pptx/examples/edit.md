# 更新可编辑幻灯片和图表

先运行检查脚本，使用返回的对象 ID 定位。幻灯片编号从 1 开始。定位组内对象时沿 `parent_id` 进入组的 `shapes`。需要保留原系列或分类时，定位图表后用 `chart.series` 和 `chart.plots[0].categories` 读取原数据。

```bash
python3 /builtin_scripts/inspect_slides.py 'input/deck.pptx' --slide 3
```

下例把分类图表数据、表格和演讲者备注写入成品。使用当前任务需要的代码块和实际 ID，传给 `sandbox_run_python` 的 `source`。散点图使用 `XyChartData`，气泡图使用 `BubbleChartData`。

```python
from pathlib import Path
from pptx import Presentation
from pptx.chart.data import CategoryChartData

deck = Presentation("input/deck.pptx")
slide = deck.slides[2]
chart = next(shape.chart for shape in slide.shapes if shape.shape_id == 5)
data = CategoryChartData()
data.categories = ["一月", "二月", "三月"]
data.add_series("实际", [120, 135, 128])
chart.replace_data(data)

table = next(shape.table for shape in slide.shapes if shape.shape_id == 6)
table.cell(1, 1).text = "135"
slide.notes_slide.notes_text_frame.text = "二月为本季度峰值。"

Path("outputs").mkdir(exist_ok=True)
deck.save("outputs/deck.pptx")
saved = Presentation("outputs/deck.pptx")
saved_chart = next(shape.chart for shape in saved.slides[2].shapes if shape.shape_id == 5)
print(list(saved_chart.series[0].values))
```

按改动页渲染。需要快速浏览多张时按页序合成一张缩略图，再用 `sandbox_view_image` 打开回执中的 `path`。细节回到对应原图。

```bash
python3 /builtin_scripts/render_document.py 'outputs/deck.pptx' 'reading/slides' --pages '2-4'
python3 /builtin_scripts/image_montage.py 'reading/overview.png' 'reading/slides/page-0002.png' 'reading/slides/page-0003.png' 'reading/slides/page-0004.png' --columns 3
```
