# 读取 PDF 指定页

页码按 PDF 物理页序（从 1 起）计算；用户引用的是印刷页码时，先定位它对应的物理页。

## 读指定页文字

```bash
pdfinfo '<输入.pdf>'
pdftotext -f <起始页> -l <结束页> -layout '<输入.pdf>' -
```

## 读表格

用 `sandbox_run_python` 传源码，`pages[<页号-1>]` 是目标页：

```python
import pdfplumber

pdf_path = "input/report.pdf"   # 换成实际路径
page_number = 1                 # 换成目标页码（从 1 起）

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[page_number - 1]
    for table in page.extract_tables():
        for row in table:
            print(row)
```

页面同时有多栏文字或并排的表格时，先按 `references/view-rendered-pages.md` 渲染该页确认每个表格的位置，再用 `page.crop((x0, top, x1, bottom))` 框选目标区域提取；坐标是 PDF 坐标，原点在左下角。保留列标题、单位和脚注。

页上有图、公式、复杂表格或文字层残缺时，按 `references/view-rendered-pages.md` 渲染查看；扫描页需要转文字时走 `references/ocr-pdf-pages.md`。


