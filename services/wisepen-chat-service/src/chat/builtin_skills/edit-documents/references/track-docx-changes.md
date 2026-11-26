# 生成与接受 Word 修订

「修订」是把改动记录成插入/删除标记（附作者和时间），让用户能在 Word 里看到、接受或拒绝，而不是直接改掉。

生成和接受是两个独立操作，通常由不同任务触发，不要自动连着做。

## 生成修订

生成是把改动标出来交给用户审阅。只有任务要求保留修订标记时，才用 `--track-author` 替换，把这次替换记录成修订；回执会返回 `revision_ids`，供后续接受用：

```bash
python3 /builtin_scripts/document/docx.py '<输入.docx>' replace '<输出.docx>' \
  --at '<at坐标>' --text '<原文>' --with '<新文>' --track-author '<作者>'
```

## 接受修订

接受是把用户选中的修订真正落地。`accept` 把选中的修订真正应用（插入的内容留下、删除的内容去掉），并移除修订标记。把要接受的修订 ID 逐个传给 `--id`：

```bash
python3 /builtin_scripts/document/docx.py '<带修订.docx>' accept '<输出.docx>' --id <id> --id <id>
```

`accept` 只处理正文和正文表格里的内联文字插入/删除，不支持移动、页眉页脚等其他类型。省略 `--id` 会接受正文里全部内联修订，只有用户明确要求时才这么做；传 `--id` 只接受指定的修订，他人的修订不受影响。
