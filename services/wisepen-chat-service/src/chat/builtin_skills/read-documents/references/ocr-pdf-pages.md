# 识别扫描页文字（OCR）

把 PDF 转成 Markdown 时（`references/convert-and-read.md`），如果文件里有扫描页（图片页、没有文字层），回执会返回 `needs_ocr`，并用 `pages` 列出这些需要 OCR 的页号。问某页的公式或图表时，先按 `references/view-rendered-pages.md` 直接看该页图；需要引用扫描正文、文字密集或图上的字读不清时，再对相关页做 OCR。

```bash
python3 /builtin_scripts/document/render_pages.py '<输入.pdf>' '<输出目录>' --pages '<页号>' --dpi 240
```

再用 Tesseract 把这张页图识别成文字，这样就能读取和引用扫描页里原本没有文字层的正文：

```bash
tesseract '<页图路径>' stdout -l chi_sim+eng
```

做整份概览时，先用文字层找正文结构，再结合页序、文档目录和前后页判断哪些扫描页需要补读；逐页抄录这类要求完整覆盖的任务，按页序补齐缺失内容。用 `pdftotext` 读原生文字，按原页序串起来；引用时给原件页码，缺页影响结论时说明范围。

