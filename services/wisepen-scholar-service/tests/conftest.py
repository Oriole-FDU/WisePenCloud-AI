"""离线部署工具独立于线上 src 包，测试按真实入口加入 deploy 路径。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
