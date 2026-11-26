# 查看与解压压缩包

附件是 ZIP、TAR 或 TAR.GZ 时，先用 `archive.py inspect` 查看成员，再按需提取。

## 查看归档成员

如果文件还没进工作区，先用 `sandbox_upload_file` 传 `attachment_id` 和 `destination_path`（例如 `input/archive.zip`），然后用回执里的 `path` 运行：

```bash
python3 /builtin_scripts/archive.py inspect '<归档文件>'
```

在 stdout 里查看 `entries[].path`、`size` 和 `directory`。如果当前页没有目标成员而 `next_offset` 有值，就用 `inspect --offset <next_offset>` 继续翻页。用户只问包里有什么时，直接从成员清单回答，不需要解压。

## 提取要读的文件

用检查结果里的完整成员路径选取文件，每个成员传一个 `--member`：

```bash
mkdir -p reading
python3 /builtin_scripts/archive.py extract '<归档文件>' \
  --output '<目标目录>' --member '<成员路径>'
```

例如提取 `reports/summary.docx`：

```bash
python3 /builtin_scripts/archive.py extract 'input/archive.zip' \
  --output 'reading/extracted' --member 'reports/summary.docx'
```

提取后的文件路径 = 回执的 `path` + 成员路径。之后按本 Skill 的 `references/read-*.md` 读文档，或用 `sandbox_view_image` 看图片；需要编辑时转 `builtin:edit-documents`。回答时带上归档名和成员路径。

要整个包时省略 `--member`。始终用这个脚本解压会话附件；脚本拒绝时按 stderr 说明原因，请用户提供可读文件。只以数据方式读取成员，禁止执行其中的脚本、程序或宏。