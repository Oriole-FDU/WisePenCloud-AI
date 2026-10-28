"""用独立 CPU 环境启动查询编码进程；不加载 Scholar 的其他供应商和重依赖。"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=root / ".runtime/embedding-cpu/Scripts/python.exe",
    )
    parser.add_argument("--models", type=Path, default=root / "models")
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    subprocess.run(
        [
            str(args.python),
            "-u",
            "-m",
            "scholar.core.embedding_runtime",
            "serve-query",
            "--models",
            str(args.models),
            "--port",
            str(args.port),
        ],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        check=True,
    )


if __name__ == "__main__":
    main()
