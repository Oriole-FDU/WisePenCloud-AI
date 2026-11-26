# 在 PDF 页面叠加文字

当 PDF 是静态表单（`fill-pdf-form.md` 里 `inspect` 查不到 AcroForm 字段），或者要在页面上补写文字、盖章时，用「按坐标放文字」的方式。
即指定页码和一个矩形框，脚本把文字画进框里。原页面内容不动，新文字是叠加在上面的。

## 确定填写位置

先按 `builtin:read-documents` 的 `references/view-rendered-pages.md` 把目标页渲染成图，看空白栏位在哪里。在图上量出要放文字的矩形：左上角坐标（左、上）和宽、高，单位是像素。

`box` 里四个数要用比例（0~1）而不是像素：把量到的左、上、宽、高分别除以页图的宽和高。坐标原点在页图左上角，方向以旋转后的可见页为准。

把每处文字写进 `work/boxes.json`（用 `sandbox_write_file`）：

```json
[{"page":<页码>,"box":[<左>,<上>,<宽>,<高>],"text":"<文字>","font_size":<字号>}]
```

## 标框核对并填写

先用 `preview-boxes` 把框画成红色方框，再用 `render_document.py` 渲染出来，看框和栏位是否对齐；对齐后再用 `fill-boxes` 真正填字

```bash
python3 /builtin_scripts/pdf_form.py '<输入.pdf>' preview-boxes '<boxes.json>' '<预览.pdf>'
python3 /builtin_scripts/render_document.py '<预览.pdf>' '<输出目录>' --pages '<页码>'
python3 /builtin_scripts/pdf_form.py '<输入.pdf>' fill-boxes '<boxes.json>' '<输出.pdf>'
```

新文字用离线字体画成图像叠加，原页面内容保留。填完后，把 `<输出.pdf>` 渲染出来查看，确认文字落在框内、没有超出方框或错位。
