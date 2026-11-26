# 渲染页图并查看

PDF、DOC、DOCX、PPT、PPTX、XLS、XLSX 都可以用 `render_document.py` 选页渲染，再用 `sandbox_view_image` 查看。

## 渲染并查看

```bash
python3 /builtin_scripts/render_document.py '<文件>' '<输出目录>' --pages '<页号>'
```

从回执的 `images[].path` 逐张调用 `sandbox_view_image`。一次最多渲染 20 页，文字小可以提高 `--dpi`（72–300）。

## 看局部

图片里某个区域看不清时，先裁出该区域再查看：

```bash
convert '<原图>' -crop '<宽>x<高>+<左>+<上>' +repage '<裁图输出>'
```

## 拼缩略图

需要快速浏览多张时，用 `image_montage.py` 拼成一张缩略图再查看：

```bash
python3 /builtin_scripts/image_montage.py '<拼图输出.png>' \
  '<页图1>' '<页图2>' '<页图3>' --columns 3
```

## 各格式看什么

- PDF/Word：核对文字流换行和表格溢出，改字后连相邻页一起看。
- PowerPoint：看标题换行、图例、重叠，结合图例、单位和图中标注。
- Excel：打印页按分页顺序编号，查某个值时回到对应工作表和单元格。

编辑后核对排版时，`builtin:edit-documents` 也会引用本文件。


