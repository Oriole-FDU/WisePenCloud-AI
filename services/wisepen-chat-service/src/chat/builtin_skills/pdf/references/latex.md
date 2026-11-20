# 用 XeLaTeX 生成 PDF

中文报告、公式、自动编号或多页表格适合直接写 LaTeX。调用 `sandbox_write_file`，用 `file` 指定 `report.tex`，用 `content` 传完整源码。示例使用可离线调用的 Noto CJK 和 Liberation 字体。插图用 `graphicx` 和工作区相对路径。

```latex
\documentclass[UTF8,fontset=none]{ctexart}
\setmainfont{Liberation Serif}
\setCJKmainfont{Noto Serif CJK SC}
\setCJKsansfont{Noto Sans CJK SC}
\usepackage{amsmath}
\begin{document}
\section{实验结果}
能量由式~\eqref{eq:energy} 给出。
\begin{equation}
  E = mc^2
  \label{eq:energy}
\end{equation}
\end{document}
```

调用 `sandbox_execute_command` 编译。包含目录或交叉引用时再编译一遍。编译失败时从 `.log` 的首条错误定位源码行，字体名称可用 `fc-match 'Noto Serif CJK SC'` 查看。

```bash
mkdir -p outputs
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error -output-directory=outputs report.tex
pdffonts outputs/report.pdf
```

用 `pdffonts` 的 `emb` 列查看字体嵌入情况，用 `pdfinfo` 获取页数，再按 PDF 选页阅读指南查看含公式和引用的成品页。交付 `outputs/report.pdf`，用户需要源文件时一起交付 `report.tex`。
