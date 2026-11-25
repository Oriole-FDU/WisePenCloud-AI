---
name: archive-reader
description: List ZIP, TAR, and TAR.GZ members or extract selected files from an archive for reading. Use when an attachment is an archive or the user asks about files inside it.
---

# 读取压缩包

文件尚未进入工作区时，调用 `sandbox_upload_file`，传当前包的 `attachment_id` 和例如 `input/archive.zip` 的 `destination_path`。用回执的 `path` 执行目录检查。

```bash
python3 /builtin_scripts/archive.py inspect 'input/archive.zip'
```

在 `sandbox_execute_command` 的 stdout 查看 `entries[].path`、`size` 和 `directory`。当前页没有目标成员且 `next_offset` 有值时，下一次调用继续 **inspect**。找到完整路径后再提取。例如回执给出 `next_offset: 12`：

```bash
python3 /builtin_scripts/archive.py inspect 'input/archive.zip' --offset 12
```

用户只问包里有什么时，从目录回答即可。

## 提取要读的文件

用检查结果里的完整成员路径选取文件。每个成员传一个 `--member`。先创建父目录 `reading`，由解压脚本创建尚未存在的 `reading/extracted`。再次提取时换一个新的目标目录名。

```bash
mkdir -p reading
python3 /builtin_scripts/archive.py extract 'input/archive.zip' \
  --output 'reading/extracted' --member 'reports/summary.docx'
```

从回执的 `path` 加成员路径得到 `reading/extracted/reports/summary.docx`。接着用 `builtin:read-markdown` 通读文档、按格式 Skill 精读，或用 `sandbox_view_image` 看图片。回答时保留档案名和成员路径。

需要整个包时，可省略 `--member`。始终用此脚本解压会话附件。脚本拒绝归档时根据 stderr 说明原因，请用户提供可读文件。仅以数据方式读取归档成员，禁止执行其中的脚本、程序和宏。

图片取出后可直接传给 `sandbox_view_image`。需要看局部时，用 `sandbox_execute_command` 生成裁图，再查看裁图路径。

```bash
convert 'reading/extracted/figures/map.png' -crop '600x400+120+80' +repage 'reading/map-detail.png'
```
