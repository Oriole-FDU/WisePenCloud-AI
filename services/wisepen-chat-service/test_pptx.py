from chat.application.utils.document_parse.parse_pptx import parse_pptx
from pathlib import Path

file_path = Path(r"C:\Users\12732\Downloads\MIXUE_Snow_King_on_Campus_FINAL_clean_editable_v2 (1).pptx")
markdown = parse_pptx(file_path, image_path=r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr\services\wisepen-chat-service\images")
output_path = Path(r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr\services\wisepen-chat-service\test_pptx.md")

output_path.write_text(markdown, encoding="utf-8")