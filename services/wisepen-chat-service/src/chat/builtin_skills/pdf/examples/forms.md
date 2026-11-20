# 填写 PDF 表单

用 `sandbox_execute_command` 检查表单。

```bash
python3 /builtin_scripts/pdf_form.py 'input/application.pdf' inspect
```

若返回字段，使用回执给出的完整字段名。按钮和下拉项使用 `choices` 的值。用 `sandbox_write_file` 写入 `work/values.json`，例如 `{"applicant":"Ada Chen","consent":"/Yes"}`。

```bash
python3 /builtin_scripts/pdf_form.py 'input/application.pdf' fill 'work/values.json' 'outputs/application.pdf'
```

按回执 `pages` 渲染填写页并查看。字段现有字体影响可显示的字符，尤其是中文。

## 填写静态表单

当字段列表为空时，用页图定位空白区域。坐标以旋转后的可见页面为准，左上角是原点。将左、上、宽、高分别除以图片宽、高，得到 `box`。示例 `work/boxes.json`：

```json
[{"page":2,"box":[0.24,0.36,0.39,0.055],"text":"陈小明","font_size":11}]
```

先生成标框 PDF，并用选页渲染和 `sandbox_view_image` 查看方框和栏位是否一致。调整 JSON 后，再填写。新增文字使用离线字体绘制成图像，PDF 原页内容保留。

```bash
python3 /builtin_scripts/pdf_form.py 'input/application.pdf' preview-boxes 'work/boxes.json' 'work/boxes.pdf'
python3 /builtin_scripts/render_document.py 'work/boxes.pdf' 'reading/form-boxes' --pages '2'
python3 /builtin_scripts/pdf_form.py 'input/application.pdf' fill-boxes 'work/boxes.json' 'outputs/application.pdf'
```

查看成品的填写页后，用 `sandbox_download_file` 交付成品。
