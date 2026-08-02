from chat.application.utils.document_parse.parse_xlsx import fast_parse_xlsx
from pathlib import Path

path_dir = Path(r"D:\xwechat_files\wxid_l1qcs8o5qb9422_ee38\msg\file\2026-06\高等数学26春.xlsx")
markdown = fast_parse_xlsx(path_dir)
output_dir = Path(r"D:\WisePenCloud-AI\WisePenCloud-AI-formal_pr\services\wisepen-chat-service\test_xlsx.md")

output_dir.write_text(markdown, encoding="utf-8")
