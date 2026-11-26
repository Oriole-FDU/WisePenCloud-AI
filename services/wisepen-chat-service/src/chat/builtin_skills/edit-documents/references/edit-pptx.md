# 编辑 PowerPoint 对象

## 定位对象

先用 `pptx.py` 定位对象 ID（幻灯片从 1 起；组内对象沿 `parent_id` 进入组）：

```bash
python3 /builtin_scripts/document/pptx.py '<演示文稿.pptx>' --slide <页码>
```

## 修改图表数据

下面的示例把分类图表的系列和分类整个替换掉（散点图用 `XyChartData`，气泡图用 `BubbleChartData`）：

```python
from pathlib import Path
from pptx import Presentation
from pptx.chart.data import CategoryChartData

deck_path = "input/deck.pptx"        # 换成实际路径
slide_number = 1                     # 换成目标页码（从 1 起）
chart_id = 1                         # 换成 inspect 回执里的图表对象 ID
categories = ["一月", "二月", "三月"]  # 换成实际分类
series_name = "实际"                 # 换成系列名
series_values = [120, 135, 128]      # 换成系列值

deck = Presentation(deck_path)
slide = deck.slides[slide_number - 1]
chart = next(shape.chart for shape in slide.shapes if shape.shape_id == chart_id)
data = CategoryChartData()
data.categories = categories
data.add_series(series_name, series_values)
chart.replace_data(data)

Path("outputs").mkdir(exist_ok=True)
deck.save("outputs/deck.pptx")
```

## 修改表格和备注

表格单元格用 `shape.table.cell(行, 列).text = 值`，备注用 `slide.notes_slide.notes_text_frame.text = 值`，都是同样的「定位对象 → 赋值 → 保存」。

## 修改文字

定位 `title.text_frame` 各段落的 `runs`，替换要改的文字。

## 新建演示

用 `slides.add_slide` 选布局，插入文本、表格和图表；库不支持的对象用 `zipfile`/`lxml` 改 OOXML。

## 核对

改完重新打开核对，按 `builtin:read-documents` 的 `references/view-rendered-pages.md` 渲染改动页，看标题换行、图例、重叠。


