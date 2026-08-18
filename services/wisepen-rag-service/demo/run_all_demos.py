"""一键重跑所有 demo，生成 output 文件供评审。"""

import subprocess
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).parent
ROOT = DEMO_DIR.parent.parent
PYTHON = sys.executable

DEMOS = [
    "structure_tree_demo.py",
    "read_content_demo.py",
    "navigation_output_demo.py",
]


def main() -> None:
    for script in DEMOS:
        print(f"=== Running {script} ===")
        result = subprocess.run(
            [PYTHON, str(DEMO_DIR / script)],
            cwd=str(ROOT),
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"FAILED: {script} (exit {result.returncode})")
            sys.exit(result.returncode)
        print()
    print("All demos completed successfully.")


if __name__ == "__main__":
    main()