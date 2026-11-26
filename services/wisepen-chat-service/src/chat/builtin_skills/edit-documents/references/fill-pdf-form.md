# 填写 PDF 表单字段

## 检查字段

先检查有没有 AcroForm 字段：

```bash
python3 /builtin_scripts/pdf_form.py '<输入.pdf>' inspect
```

## 填写字段

有字段时，用回执里的完整字段名填写；按钮和下拉项用 `choices` 里的值。用 `sandbox_write_file` 写 `work/values.json`：

```json
{"<字段名>":"<值>"}
```

```bash
python3 /builtin_scripts/pdf_form.py '<输入.pdf>' fill '<values.json>' '<输出.pdf>'
```

按回执里的 `pages` 渲染填写页核对。字段自带的字体会限制能显示的字符，中文尤其容易受影响。没有字段的静态表单走 `references/overlay-pdf.md`。


