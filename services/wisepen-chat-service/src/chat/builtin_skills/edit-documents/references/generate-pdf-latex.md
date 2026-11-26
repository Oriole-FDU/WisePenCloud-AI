# 用 XeLaTeX 生成 PDF

用户要一份带中文、数学公式、自动编号（章节/公式/交叉引用）或多页表格的 PDF 时，直接写 LaTeX 源再编译，比从 Word/HTML 转更可控。XeLaTeX 是能直接处理中文和系统字体的 LaTeX 引擎。

## 写 LaTeX 源

用 `sandbox_write_file` 写 `report.tex`。下面是含中文和公式的最小可编译示例：

```latex
\documentclass[UTF8,fontset=none]{ctexart}  % 中文文档类 ctexart；UTF8 编码，字体自行指定
\setmainfont{Liberation Serif}              % 西文正文字体
\setCJKmainfont{Noto Serif CJK SC}          % 中文正文字体
\setCJKsansfont{Noto Sans CJK SC}           % 中文无衬线字体
\usepackage{amsmath}                         % 数学公式和自动编号
\begin{document}
\section{实验结果}
能量由式~\eqref{eq:energy} 给出。
\begin{equation}
  E = mc^2
  \label{eq:energy}
\end{equation}
\end{document}
```

- 中文用 `ctexart` 文档类；`fontset=none` 表示字体由下面的 `\setCJK...` 显式指定（镜像已装 Noto CJK、Liberation 等离线字体）。
- 公式用 `amsmath`；`\label` 标记、`\eqref` 引用，实现自动编号和交叉引用。

## 编译并检查

```bash
mkdir -p outputs
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error -output-directory=outputs report.tex
pdffonts outputs/report.pdf
```

- `-no-shell-escape` 禁止编译时执行外部命令；`-interaction=nonstopmode -halt-on-error` 出错即停；`-file-line-error` 让报错带上源文件行号；`-output-directory` 指定输出目录。
- 有目录或交叉引用时编译两遍，编号才稳定。
- `pdffonts` 的 `emb` 列确认字体已嵌入；中文 PDF 字体没嵌入，换台机器打开会缺字或乱码。

编译失败时从 `.log` 的第一条错误定位源文件行号；字体名可用 `fc-match 'Noto Serif CJK SC'` 查。成品是 `outputs/report.pdf`，用户要源文件时一起给 `report.tex`。


