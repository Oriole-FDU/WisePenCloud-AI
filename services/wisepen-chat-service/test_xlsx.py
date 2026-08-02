from chat.application.utils.document_parse.parse_xlsx import parse_xlsx
from pathlib import Path

file_path = Path(r"D:\xwechat_files\wxid_l1qcs8o5qb9422_ee38\msg\file\2026-06\高等数学26春.xlsx")
markdown = parse_xlsx(file_path)
output_path = Path(r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr\services\wisepen-chat-service\test_xlsx.md")

output_path.write_text(markdown, encoding="utf-8")
