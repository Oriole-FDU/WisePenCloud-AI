# PDF 选页阅读

用 `sandbox_execute_command` 运行下面的命令。页码取 PDF 的物理页序，从 1 开始。若用户引用的是印刷页码，先定位它对应的物理页。

```bash
pdfinfo 'input/report.pdf'
pdftotext -f 8 -l 9 -layout 'input/report.pdf' -
```

有图、公式、复杂表格或文字层残缺时，将相关页渲染成图片。一次最多选 20 页。读回执的 `images[].path`，用 `sandbox_view_image` 的 `path` 逐张查看。

```bash
python3 /builtin_scripts/render_document.py 'input/report.pdf' 'reading/pdf-pages' --pages '8,10-11'
```

## 扫描页

AnyDoc 回执的 `pages` 给出扫描候选页。问某页的公式或图表时，直接查看该页图。需要引用扫描正文、文字密集或页图上的字读不清时，对相关页用 Tesseract。做整份概览时，先用文字层找正文结构，再结合页序、目录和前后页判断哪些扫描页需要补读。逐页抄录等要求完整覆盖的任务，按页序补齐缺失内容。

例如第 8 页包含用户要看的公式，第 11 页包含要引用的扫描正文。这两页可以一次渲染。文字较小时可提高 DPI。

```bash
python3 /builtin_scripts/render_document.py 'input/report.pdf' 'reading/pdf-ocr' --pages '8,11' --dpi 240
```

```bash
tesseract 'reading/pdf-ocr/page-0011.png' stdout -l chi_sim+eng
```

用回执里的 `images[].path` 打开第 8 页（`sandbox_view_image`），对第 11 页运行上面的 OCR 命令。用 `pdftotext` 读取相关原生文字，按原页序串起结果。需要引用时给原件页码，有缺页影响结论时说明范围。

## 表格和坐标

把下面的 Python 代码作为 `sandbox_run_python` 的 `source`，读取第 8 页的表格。多栏或相邻表格混在一起时，先用页图确认区域，再对 `page.crop((x0, top, x1, bottom))` 提取。保留列标题、单位和脚注。

```python
import pdfplumber

with pdfplumber.open("input/report.pdf") as pdf:
    page = pdf.pages[7]
    for table in page.extract_tables():
        for row in table:
            print(row)
```
