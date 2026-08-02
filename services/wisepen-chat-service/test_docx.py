from chat.application.utils.document_parse.parse_docx import parse_docx
from pathlib import Path

file_path = Path(r"C:\Users\12732\Downloads\复旦_中国近现代史纲要_期末开卷资料_教材页码定位_选择题论述题完整版.docx")

markdown = parse_docx(
    file_path,
    image_path="output/images",
)

output_path = Path(r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr\services\wisepen-chat-service\test_docx.md")

markdown = parse_docx(
    file_path,
    image_path= Path(r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr\services\wisepen-chat-service") / "images",
)

output_path.write_text(markdown, encoding="utf-8")
print(f"Markdown saved to {output_path}")