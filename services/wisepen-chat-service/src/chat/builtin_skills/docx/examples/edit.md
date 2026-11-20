# 定位并编辑 Word 原文

用 `sandbox_execute_command` 执行下面的命令，并从回执 `paragraphs[].at` 取位置。位置按 1 起算。顶层 `12` 表示正文第 12 个对象为段落，`8:2:3:1` 表示正文第 8 个对象是表格，目标为第 2 行第 3 列第 1 个对象。嵌套表格继续延伸这套编号。重复匹配时缩小到一个准确的短语。

```bash
python3 /builtin_scripts/docx_document.py 'input/report.docx' inspect --contains '项目初稿'
python3 /builtin_scripts/docx_document.py 'input/report.docx' replace 'outputs/report.docx' --at '12' --text '项目初稿' --with '项目定稿'
```

文字可跨普通 run。替换按原 run 分配样式，额外字符沿用最后一个命中 run 的样式。涉及超链接、字段、已有修订、绘图或跨段落的修改时，先定位具体对象，再按需用 `python-docx` 或 OOXML 修改。换行与结构编辑可以直接用 `python-docx`。

## 批注和修订

沿用检查回执中找到的 `at`。原生批注锚定到指定字串。只有任务要求保留修订时才传 `--track-author`。

```bash
python3 /builtin_scripts/docx_document.py 'input/report.docx' comments
python3 /builtin_scripts/docx_document.py 'input/report.docx' add-comment 'outputs/commented.docx' --at '12' --text '项目初稿' --body '请补充发布日期' --author '编辑'
python3 /builtin_scripts/docx_document.py 'input/report.docx' replace 'outputs/tracked.docx' --at '12' --text '项目初稿' --with '项目定稿' --track-author '编辑'
```

`accept` 适用于正文和正文表格中的内联文字插入与删除。接受刚创建的替换时，把该次回执的全部 `revision_ids` 分别传给 `--id`。下例的 ID 用实际回执替换。只有用户要求接受正文全部内联修订时才省略 `--id`。

```bash
python3 /builtin_scripts/docx_document.py 'outputs/tracked.docx' accept 'outputs/accepted.docx' --id 4 --id 5
```

批注用 `comments` 重查，普通替换用 `inspect --contains` 查看结果。涉及排版时按原件渲染指南看对应页。
