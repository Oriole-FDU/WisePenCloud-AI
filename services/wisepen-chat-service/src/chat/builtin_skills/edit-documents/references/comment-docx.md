# 给 Word 加批注

先用 `references/locate-docx.md` 定位，再把原生批注锚定到指定字串：

```bash
python3 /builtin_scripts/document/docx.py '<输入.docx>' add-comment '<输出.docx>' \
  --at '<at坐标>' --text '<锚定文字>' --body '<批注内容>' --author '<作者>'
```

回读现有批注：

```bash
python3 /builtin_scripts/document/docx.py '<输入.docx>' comments
```

批注加完后用 `comments` 重查核对。

