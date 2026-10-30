"""离线导入、每日 OAI + S2 同步及发布的命令入口。"""

from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    load_dotenv(env_path)

from offline.cli import main

if __name__ == "__main__":
    main()
