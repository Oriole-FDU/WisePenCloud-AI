"""看板通过真实子进程退出码区分成败，HTTP 展示原始失败日志。"""

import json
import subprocess
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "offline_dashboard.py"


@pytest.mark.parametrize("exit_code,status", [(0, "succeeded"), (7, "failed")])
def test_job_exit_progress_and_http_log(tmp_path, exit_code, status):
    # 保护的是真实任务失败可见性，不能以存在一条进度或 pending=0 推断成功。
    child = (
        "import json,sys; "
        "print(json.dumps({'stage':'s2_projection','staged_changed':12,'committed':False})); "
        "print('真实错误：模拟外部写入失败' if int(sys.argv[1]) else '任务完成'); "
        "sys.exit(int(sys.argv[1]))"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "run",
            "example",
            "--",
            sys.executable,
            "-c",
            child,
            str(exit_code),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert result.returncode == exit_code, result.stderr
    state = json.loads((tmp_path / "example.json").read_text(encoding="utf-8"))
    assert state["status"] == status
    assert state["exit_code"] == exit_code
    assert state["stages"]["s2_projection"]["event"]["staged_changed"] == 12
    assert state["stages"]["s2_projection"]["event"]["committed"] is False

    with subprocess.Popen(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), "serve", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as server:
        try:
            url = server.stdout.readline().strip().split()[-1]
            opener = build_opener(ProxyHandler({}))
            with opener.open(url + "/api/status", timeout=5) as response:
                payload = json.load(response)
            assert payload["jobs"][0]["status"] == status
            assert payload["jobs"][0]["exit_code"] == exit_code
            expected = "真实错误：模拟外部写入失败" if exit_code else "任务完成"
            assert expected in payload["jobs"][0]["log"]
            with opener.open(url, timeout=5) as response:
                assert "离线任务运行台" in response.read().decode("utf-8")
        finally:
            server.terminate()
            server.wait(timeout=5)
