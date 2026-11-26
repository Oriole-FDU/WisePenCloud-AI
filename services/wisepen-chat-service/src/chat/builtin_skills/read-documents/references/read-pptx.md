# 读取 PowerPoint 内容

## 读对象与备注

读取指定幻灯片的对象名、正文、表格和备注：

```python
from pptx import Presentation

deck_path = "input/slides.pptx"  # 换成实际路径
slide_number = 1                 # 换成目标页码（从 1 起）

slide = Presentation(deck_path).slides[slide_number - 1]
for shape in slide.shapes:
    print(shape.name)
    if shape.has_text_frame:
        print(shape.text)
    if shape.has_table:
        for row in shape.table.rows:
            print([cell.text for cell in row.cells])
if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
    print("备注", slide.notes_slide.notes_text_frame.text)
```

## 读图表数据

读图表数据时按对象名定位 `shape.chart`，查看系列和分类。旧版 PPT 的文本先用 `references/convert-and-read.md` 转 Markdown；需要读对象或备注时转成 PPTX 副本。看布局按 `references/view-rendered-pages.md`。


