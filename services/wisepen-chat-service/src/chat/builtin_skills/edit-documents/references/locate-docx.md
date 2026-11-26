# 编辑前定位 Word 内容

替换、批注、修订这些编辑操作，都要先定位目标、拿到 `at` 坐标。用 `docx.py inspect` 定位目标段落或表格：

```bash
python3 /builtin_scripts/document/docx.py '<输入.docx>' inspect --contains '<关键词>'
```

位置从 1 起。顶层 `12` 表示正文第 12 个对象是段落；`8:2:3:1` 表示正文第 8 个对象是表格、目标在第 2 行第 3 列第 1 个对象；嵌套表格继续延伸这套编号。重复匹配时把关键词缩小到唯一短语。

拿到的 `at` 坐标传给 `replace`、`comment`、`track`。替换、批注或修订之后，用 `inspect --contains` 重查核对结果。

