# 读取指定幻灯片

下面的代码可作为 `sandbox_run_python` 的 `source`，列出第三张 PPTX 幻灯片的对象名、正文、表格和备注。读取图表数据时按对象名定位 `shape.chart`，查看系列和分类。旧 PPT 的文本可先用 AnyDoc，需读取对象或备注时转 PPTX 工作副本。

```python
from pptx import Presentation

slide = Presentation("input/slides.pptx").slides[2]
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

## 看幻灯片布局

在 `sandbox_execute_command` 运行渲染命令。PPT 和 PPTX 都可用，渲染保留隐藏页的序号。用 `sandbox_view_image` 的 `path` 打开回执里的 `images[].path`。

```bash
python3 /builtin_scripts/render_document.py 'input/slides.pptx' 'reading/slides' --pages '3,6-7'
```

看图表时结合图例、单位和图中标注。用户需要引用时给源幻灯片序号和对象或备注位置。
