from __future__ import annotations

from pathlib import Path
from time import perf_counter

from docling.document_converter import DocumentConverter
from docling_core.types.doc import ImageRefMode


INPUT_PATH = Path(
    r"C:\Users\12732\Downloads\自定义搜索_API_Key_配置指南 (1).docx"
)

OUTPUT_DIR = Path(
    r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr"
    r"\services\wisepen-chat-service"
)

OUTPUT_PATH = OUTPUT_DIR / f"{INPUT_PATH.stem}.md"

# 引用图片的实际保存目录
ARTIFACTS_DIR = OUTPUT_DIR / f"{INPUT_PATH.stem}_artifacts"


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(f"输入文件不存在：{INPUT_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    converter = DocumentConverter()

    started_at = perf_counter()

    print(f"正在解析：{INPUT_PATH}")
    result = converter.convert(INPUT_PATH)

    print(f"识别到图片数量：{len(result.document.pictures)}")

    # REFERENCED 模式不能再只使用 export_to_markdown + write_text，
    # 应让 Docling 同时保存 Markdown 和外部图片。
    result.document.save_as_markdown(
        filename=OUTPUT_PATH,
        image_mode=ImageRefMode.REFERENCED,
        artifacts_dir=ARTIFACTS_DIR,
    )

    elapsed = perf_counter() - started_at
    markdown_size = OUTPUT_PATH.stat().st_size

    print("解析完成")
    print(f"Markdown 文件：{OUTPUT_PATH}")
    print(f"图片资源目录：{ARTIFACTS_DIR}")
    print(f"识别图片数量：{len(result.document.pictures)}")
    print(f"Markdown 文件大小：{markdown_size:,} 字节")
    print(f"耗时：{elapsed:.2f} 秒")


if __name__ == "__main__":
    main()