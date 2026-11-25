---
name: pptx
description: Read or edit individual PowerPoint slides, shapes, tables, charts, and speaker notes. Create editable PPTX decks and work from legacy PPT. Use read-markdown for deck-wide reading.
---

# PowerPoint

- 通读整套课件或梳理叙事结构，加载 `builtin:read-markdown`。
- 查指定幻灯片、图表、备注或版式，用 `load_skill_asset` 打开本 Skill 的 `references/reading.md`。
- 编辑已有演示时先用幻灯片序号定位，再从 `slide.shapes` 找对象。使用模板时，从原稿的版式和占位符开始。

附件用 `sandbox_upload_file` 导入后取回执的 `path`。旧版 PPT 需要编辑时，用 `sandbox_execute_command` 转成 PPTX 工作副本。

```bash
mkdir -p work
soffice --headless --convert-to pptx --outdir work 'input/slides.ppt'
```

## 编辑对象

先用 `python3 /builtin_scripts/inspect_slides.py 'input/slides.pptx' --slide 3` 取得对象 ID、名称和类型。更新图表、表格或备注时，用 `load_skill_asset` 打开 `examples/edit.md`。渲染多张后，可用其中的拼图命令快速查看整组页图。

用 `sandbox_run_python` 的 `source` 传原始多行 Python。示例在第三张幻灯片修改已确认的标题文本 run。

```python
from pathlib import Path
from pptx import Presentation

deck = Presentation("input/slides.pptx")
title = deck.slides[2].shapes.title
run = next(run for paragraph in title.text_frame.paragraphs
           for run in paragraph.runs if "阶段总结" in run.text)
run.text = run.text.replace("阶段总结", "阶段成果", 1)
Path("outputs").mkdir(exist_ok=True)
deck.save("outputs/slides.pptx")
```

新建时用 `slides.add_slide` 选布局，插入可编辑文本、表格和图表。用 `python-pptx` 的 `shape.table` 改单元格、`shape.chart.replace_data` 换图表数据、`slide.notes_slide.notes_text_frame` 改备注。需要处理库未提供的对象时，用 `zipfile` 与 `lxml` 读取相关 OOXML 部件和关系。需要裁图时用 Pillow。

重新打开成品核对页序和改动对象，按精读指南渲染改动幻灯片，看标题换行、图例、重叠或溢出。用 `sandbox_download_file` 返回成品的下载链接。
