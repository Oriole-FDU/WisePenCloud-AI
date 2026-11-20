# PowerPoint 精读

PPTX 用 `python-pptx` 按源幻灯片序号检查正文、表格与演讲者备注。旧版 PPT 用 AnyDoc 原生读取，需核对版式时定向渲染。回复中单独标明备注内容。

```bash
python /builtin_scripts/render_document.py 'input/slides.ppt' 'reading/slides' --pages 3,8
```

脚本通过 LibreOffice 导出包含隐藏页的 PDF，再渲染选中的页。回执路径可交给 `sandbox_view_image`。需要改导出参数时可直接用 `soffice --headless --convert-to pdf:impress_pdf_Export`，使用独立临时 profile 和输出目录。

## 查看指定幻灯片

下面的源码可直接传给 `sandbox_run_python` 的 `source`。

```python
from pptx import Presentation

slide = Presentation("input/slides.pptx").slides[2]
for shape in slide.shapes:
    if shape.has_text_frame:
        print(shape.text)
    if shape.has_table:
        for row in shape.table.rows:
            print([cell.text for cell in row.cells])
if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
    print("演讲者备注：", slide.notes_slide.notes_text_frame.text)
```

幻灯片序号从 1 开始，Python 下标从 0 开始。遇到组合形状时按需检查其子元素。阅读图表先核实标题、单位、图例与数据标签，再看源图表数据或选中页图。备注和画面文字分别归属，跨页结论记录所依据的幻灯片。
