# PDF 与 Office 文档读取实施计划

**状态：方案 2 已获准实施。开发分支：`codex/sandbox-development`。**

## 目标与范围

用户在 WisePen Web 会话上传 PDF、DOC/DOCX、PPT/PPTX、XLS/XLSX 或 CSV 后，可以提问、总结、跨文件比较，并按需回原件核对页码、段落、幻灯片或单元格。图表、公式和版式由模型按需查看选中页。处理在 Linux AIO 容器内离线完成。文档创建、改写和原格式编辑在读取能力验收后另行设计。

首版沿用聊天附件入口、沙箱命令和会话工作区。文件存储里的原附件是来源，Markdown、OCR 文本和页图是可重建的会话产物，随现有工作区生命周期管理。旧版 Office 由 AnyDoc 原生转换，版式用 LibreOffice 核对，XLS 单元格保存值由 xlrd 读取。

## 主链路与契约

```text
Web 会话附件 → read-markdown Skill → sandbox_upload_file
  → sandbox_execute_command 调 convert_document → 工作区 Markdown
  → Bash 定位标题、关键词和行号 → 分段取回证据
  → 按需用格式指南核对原件 → 必要时渲染并调用 sandbox_view_image
```

- **输入。** Skill 使用当前会话的 `attachment_id` 导入文件，采用无业务歧义的工作区内路径。多附件各自有输出位置，同名附件也能独立读取。脚本读取导入后的本地路径，调用时正确引用或转义路径参数。
- **通用转换。** 在 `services/wisepen-sandbox-service/src/sandbox/builtin_scripts/convert_document.py` 封装 AnyDoc 本地 Python 接口。接受源文件和目标 Markdown 路径，保持 UTF-8 和解析器提供的标题、列表、表格等结构。成功时向 stdout 输出一条简短回执，包含工作区相对路径、识别格式，以及有缺口时的具体范围。用户可处理的错误以非零退出码和可读原因返回，例如格式不支持、文件损坏、文件加密、大小超限。只有已写成的产物才返回路径。
- **PDF 扫描页。** AnyDoc 报告 `NeedsOcrError` 时，回执给出需要 OCR 的页。先通过目标版本和混合 PDF 样例核定页号约定。仅在需要覆盖这些页时按 PDF 指南补读。若 AnyDoc 未提供混合文件的部分 Markdown，PDF 指南用 Poppler 取原生文本页，按需求对扫描页做 OCR，最后按原页序汇总证据。OCR 结果注明来源，空页或识别失败给出明确范围。普通文本转换只调用 AnyDoc。
- **缓存。** `sandbox_read_file.content` 与命令 `stdout`、`stderr` 采用 `ITEMS`，适合行段预览。全文留在工作区，只送必要的行段给 Chat。Skill 据缓存提示或 `output_truncated` 缩小范围重读。若命令只返回预览，也按实际收到的范围判断阅读覆盖。章节缓存方案单列在 [TODO.md](TODO.md)。
- **Skill 注册。** `src/chat/builtin_skills/` 下提供独立的 `read-markdown`，以及 `pdf`、`docx`、`pptx`、`xlsx` 格式专用 Skill，沿用 `load_skill` 和 `load_skill_asset`。在打包镜像里确认各入口和精读参考可用。

## Skill 结构与工具选择

| 入口 | 内容与职责 |
| --- | --- |
| `read-markdown/SKILL.md` | 已有 Markdown 的定向阅读，以及离线文档转换和长文分段。 |
| `pdf/SKILL.md` | 选页提取、扫描页 OCR、PDF 结构修改、报告生成。 |
| `docx/SKILL.md` | 按章节精读，编辑段落、run、样式和表格，按页检查排版。 |
| `pptx/SKILL.md` | 按源幻灯片及备注核对，保留模板和可编辑对象，渲染受影响页。 |
| `xlsx/SKILL.md` | 按工作表或范围核对原始值和公式，编辑后重开产物并验证关键计算。 |

脚本封装反复使用且需要稳定输出的流程：通用转换、选页渲染、工作簿范围读取。单页提取、定向 OCR、复杂表格检查和调整导出参数保留原生 CLI 或库用法。`render_document.py` 复用本机选页渲染脚本里的 PDFium 部分，Office 分支使用 `soffice --headless`，每次转换有独立的临时 profile 和输出目录。渲染回执包含原件、输出页号和页图相对路径，保留源文件。

本机参照为 `~/.agents/skills/convert-documents-to-markdown/SKILL.md`、`~/.pi/agent/skills/read-markdown/SKILL.md`、`~/.agents/skills/pdf/SKILL.md` 及其 `scripts/render_pages.py`。运行所需依赖按 Linux 镜像选型，桌面渲染方式不参与实现。

## 页图进入模型

模型先根据渲染回执选图，再显式调用 `sandbox_view_image(path)`。工具读取当前会话工作区内的 PNG、JPEG、WebP、GIF、BMP、TIFF、ICO 或 AVIF。Pillow 根据内容解码，限制字节和像素，取动画或多页图片的首帧，统一输出 PNG 或 JPEG。文本回执提供路径及原格式，MCP `ImageContent` 提供图片。Chat 复用已有图片通道。后续轮次需要重新看图时按路径再次调用。

这一步是精读阶段的独立 MCP 能力。方案 2 的**通用文档读取**全程复用既有 MCP 工具，页图查看仅在模型需要视觉证据时调用。对无视觉能力或无法接收工具图片的模型，用原文或 OCR 继续处理可读部分，并明确视觉证据缺口。

## 离线镜像与发布

在 AIO 派生镜像中固定经验证的 `firecrawl-anydoc` 版本，依据目标 Linux 架构和 Python 版本验证可用 wheel。镜像交付 Poppler、PDFium、Pillow、Tesseract 及中英语料、LibreOffice、XeLaTeX、中文宏包和 Office Python 库。Noto CJK、Liberation、Carlito、Caladea 随镜像安装。构建时编译中文 PDF 并检查字体嵌入。执行容器使用本地文件与已安装依赖。

部署先交付携带 Skill 的 Chat 镜像和携带脚本及派生 Dockerfile 的 Sandbox 服务镜像，再让新预热容器加载脚本。验证环境关注实际选中的派生镜像和新容器编号，因为旧预热容器仍使用创建时的脚本与依赖。部署主机也离线时，由构建环境提前生成 AIO 镜像，使用 `docker save` / `docker load` 运送到实际 Docker daemon，Nacos 配置指向固定镜像版本并设置 `derive_image: false`。

直接验证脚本时，可用 `--network none` 启动独立容器。Web 联调仍使用 WisePen 服务与 AIO 之间的内部通信，验收执行容器无外网出口。

## 实施顺序与验收

| 阶段 | 交付 | 验收标准 |
| --- | --- | --- |
| 1. 通用阅读 | AnyDoc 镜像依赖、`convert_document.py`、内置 Skill、Bash 阅读指南 | 断网容器读通八种首版格式，输入与输出路径可用，命令只返回短回执。用同一份长 PDF 验证局部问答与全文总结，确认后半部分可读、回答能指回原文。加同名附件跨文件比较及加密、损坏输入。记录耗时、峰值内存和长文工具调用次数作为基线。 |
| 2. 精确读取 | 各格式参考指南、`read_workbook.py`、按需 PDF OCR | 混合中英扫描 PDF 保留原页顺序，能区分文本层和 OCR 证据。DOCX 按章节定位、PPTX 核对幻灯片及备注、XLSX 按范围核对公式与缓存值，包括缺少缓存值的情况。 |
| 3. 选页渲染与看图 | `render_document.py`、`sandbox_view_image` | PDF、中文 Word 和带图表的 PPT 在 Linux 生成正确选中页。用真实 Web 会话验证相关图像通过 MCP 到达目标模型，模型能回答只可从图中得到的问题。验证同一张图能按需重读，且工具的工作区和图片限制生效。 |

## 压缩包附件

`archive-reader` 单独指导 ZIP、TAR、TAR.GZ 的检查和按需解压。通过 `archive.py inspect` 分页浏览成员与展开大小，再用 `archive.py extract` 选择需要的文件、写入新工作区目录。两种操作使用同一套归档限制。终点是可安全交给文档或图片工具的工作区路径，压缩包及成员内容按不可信数据处理。验收覆盖正常检查、选择性解压、拒绝危险路径和链接、超限及解压失败后的输出目录清理。

读取验收通过后进入编辑设计。实际 Web 会话、目标 AIO 基础镜像及部署网络策略分别验收。
