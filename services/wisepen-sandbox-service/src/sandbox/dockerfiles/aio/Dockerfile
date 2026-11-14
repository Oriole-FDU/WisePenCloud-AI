# syntax=docker/dockerfile:1

# =============================================================================
# 派生沙箱镜像，在第三方 AIO 基础镜像上叠加 WisePen 自有的工具链
# -----------------------------------------------------------------------------
# 构建契约（由 sandbox-service 的 ImageBuilder 传入）：
#   BASE_IMAGE            基础镜像（来自 Nacos 配置 provider.settings.AIO.image）
#   SANDBOX_RUNTIME_USER  基础镜像的运行时用户（docker image inspect {{.Config.User}}）
#
# 派生镜像 tag 由「基础镜像 digest + 本文件内容」的 hash 寻址，本文件变更即触发重建
# =============================================================================

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# 基础镜像默认以非 root 用户运行
# 安装系统包需 root，构建末尾再恢复运行时用户
USER root

ENV DEBIAN_FRONTEND=noninteractive

# ---- LibreOffice（重且稳定，单独成层；含 recommends 保证字体/滤镜齐全） ----
RUN apt-get update \
 && apt-get install -y libreoffice \
 && rm -rf /var/lib/apt/lists/*

# ---- 系统级工具（apt） ----
#   pandoc（命令 pandoc）：通用文档格式转换器，用于 Markdown/HTML/LaTeX/DOCX/EPUB 等格式互转。
#   poppler-utils（命令 pdftotext/pdftoppm/pdfimages/pdffonts/pdfinfo）：PDF 转文本/位图、提取内嵌图片与字体、读取元数据。pdf2image、pytesseract 调用。
#   qpdf（命令 qpdf）：PDF 结构级处理：合并/拆分/加解密/压缩/修复。pikepdf 底层依赖 libqpdf。
#   tesseract-ocr（命令 tesseract）：OCR 引擎，从图像识别文本。pytesseract 是对它的 Python 封装。
#   tesseract-ocr-chi-sim（语言数据包）：tesseract 的简体中文识别数据。
#   imagemagick（命令 convert）：ImageMagick 6，图像格式转换/缩放/裁剪/合成/水印。
#   ffmpeg（命令 ffmpeg、ffprobe）：音视频转码、截取、压缩、提取音轨、生成缩略图/动图。
#   ghostscript（命令 gs）：PostScript/PDF 解释器；camelot-py、ocrmypdf、pdfkit 依赖它处理 PDF。
#   mupdf-tools（命令 mutool）：轻量 PDF 处理：内容提取、转换、清理、结构检查。
#   default-jre-headless（命令 java）：无图形界面的 Java 运行时。tabula-py 依赖。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      pandoc \
      poppler-utils \
      qpdf \
      tesseract-ocr \
      tesseract-ocr-chi-sim \
      imagemagick \
      ffmpeg \
      ghostscript \
      mupdf-tools \
      default-jre-headless \
 && rm -rf /var/lib/apt/lists/*

# ---- Python 库（pip） ----
#   python-docx：读写 Microsoft Word（.docx）文档。
#   openpyxl：读写 Excel（.xlsx）工作簿。
#   xlsxwriter：生成 Excel（.xlsx）文件。
#   python-pptx：读写 PowerPoint（.pptx）演示文稿。
#   odfpy：读写 OpenDocument（.odt/.ods/.odp）格式。
#   pandas：表格数据分析与处理。
#   pypdf：读取、合并、拆分、旋转 PDF，提取文本与元数据。
#   pdfplumber：PDF 文本与表格提取，解析复杂布局。
#   pdfminer.six：PDF 文本提取底层库。
#   pypdfium2：将 PDF 渲染为图像（基于 PDFium）。
#   pikepdf：PDF 底层结构编辑。依赖 libqpdf。
#   camelot-py：PDF 表格提取。依赖 ghostscript 与 opencv。
#   tabula-py：PDF 表格提取。依赖 Java。
#   reportlab：程序化生成 PDF。
#   img2pdf：将图片无损封装为 PDF。
#   pdfkit：将 HTML 转 PDF。依赖 wkhtmltopdf（当前缺失）。
#   markitdown：将 PDF/DOCX/PPTX 等文档转为 Markdown。
#   pytesseract：tesseract OCR 的 Python 封装。
#   pdf2image：PDF 转图像。依赖 poppler。
#   pillow：图像处理基础库。
#   opencv-python-headless：计算机视觉库的无 GUI 版本。camelot-py 依赖。
#   lxml：XML/HTML 解析与处理。
#   beautifulsoup4：HTML/XML 解析与内容提取。
#   markdownify：HTML 转 Markdown。
#   defusedxml：安全解析 XML，防 XXE 攻击。
#   Jinja2：模板引擎。
#   Markdown：Markdown 转 HTML。
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install \
      python-docx openpyxl xlsxwriter python-pptx odfpy pandas \
      pypdf pdfplumber pdfminer.six pypdfium2 pikepdf camelot-py tabula-py \
      reportlab img2pdf pdfkit \
      markitdown \
      pytesseract pdf2image pillow opencv-python-headless \
      lxml beautifulsoup4 markdownify defusedxml Jinja2 Markdown

# 恢复基础镜像的运行时用户，保证派生镜像的进程身份与基础镜像一致。
ARG SANDBOX_RUNTIME_USER=root
USER ${SANDBOX_RUNTIME_USER}