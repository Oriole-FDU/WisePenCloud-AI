# PDF 精读

用原件的物理页序定位。局部文字保留页号，必要时用 `pdfplumber` 核对表格与坐标。

```bash
pdfinfo 'input/report.pdf'
pdftotext -f 8 -l 9 -layout 'input/report.pdf' -
python /builtin_scripts/render_document.py 'input/report.pdf' 'reading/pdf-pages' --pages 8-9
```

视觉问题选择回执中的相关 PNG 调用 `sandbox_view_image`。需要更清晰的扫描文字时，可在新目录里用 `--dpi 240` 重新选页渲染，再执行：

```bash
tesseract 'reading/pdf-ocr-pages/page-0008.png' stdout -l chi_sim+eng
```

AnyDoc 的 `needs_ocr` 回执给出一基页号。按用户问题选择页码，对全文任务按原页序组合原生文本和扫描页 OCR。原生页用 Poppler 取回，扫描页单独识别。将 OCR 标为识别结果，空页或识别不清时说明具体页。图表与公式以选页图为依据。

## 表格与坐标

先用 `pdftotext -layout` 判断列与文字层的排列。需要核对具体值时，在对应页用 `pdfplumber` 读取整张表，保留表头、单位和脚注。表头跨页时同时查看前一页。文字层提取结果混乱时看页图，逐列核对。

下面的源码可直接传给 `sandbox_run_python` 的 `source`。

```python
import json
import pdfplumber

with pdfplumber.open("input/report.pdf") as pdf:
    page = pdf.pages[7]
    table = page.extract_table()
    print(json.dumps({"page": 8, "first_rows": table[:12] if table else []}, ensure_ascii=False))
```

需要定位页内区域时用 `page.crop((x0, top, x1, bottom))` 后提取文字或表格，坐标以原页为基准。需要核对嵌入图片时可用 `pdfimages -f 8 -l 8 -list 'input/report.pdf'`，图例、标签或矢量内容以整页渲染为准。页图过细时先用 `--dpi` 提高清晰度或裁剪局部，再调用看图工具核实。
