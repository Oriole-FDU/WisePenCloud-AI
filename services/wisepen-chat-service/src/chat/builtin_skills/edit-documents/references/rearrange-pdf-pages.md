# 抽取、合并与旋转 PDF 页面

用 `qpdf` 操作页面：

```bash
mkdir -p outputs
qpdf --empty --pages '<输入.pdf>' <起始页>-<结束页> -- '<输出.pdf>'      # 抽取
qpdf --empty --pages '<A.pdf>' 1-z '<B.pdf>' 1-z -- '<输出.pdf>'        # 合并
qpdf '<输入.pdf>' --rotate=<角度>:<页号> -- '<输出.pdf>'                 # 旋转
pdfinfo '<输出.pdf>'
```

复杂页序用 `pypdf.PdfWriter`。改完读输出页数和页序；视觉内容按 `builtin:read-documents` 的 `references/view-rendered-pages.md` 核对。

