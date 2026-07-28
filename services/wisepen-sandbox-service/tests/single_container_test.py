"""
单容器一致性测试 — 获取一个容器后连续执行所有操作，验证文件持久化。

用法：
    cd AI
    uv run uvicorn sandbox.gateway.main:app --host 0.0.0.0 --port 8001

    cd AI/services/wisepen-sandbox-service
    uv run python tests/single_container_test.py
"""
import asyncio
import httpx
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "wisepen-common", "src"))

GATEWAY = os.getenv("SANDBOX_GATEWAY", "http://127.0.0.1:8001")
HEADERS = {
    "X-From-Source": "local-dev-secret",
    "X-User-Id": "u1",
    "X-Session-Id": "s1",
    "Content-Type": "application/json",
}
UID = "u1"
SID = "s1"


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        base = f"{GATEWAY}/v1/sandbox/gateway"

        # ============================================================
        # Step 1: 获取并锁定一个容器
        # ============================================================
        print("=" * 60)
        print("[1] Acquiring container via VNC binding (session affinity)...")
        # VNC binding 有会话亲和性 — 同一个 (uid, sid) 绑定到同一个容器
        # 但我们不走 VNC，直接通过 REST API 多次调用：
        # 第一次调用写文件，第二次调用读同一个容器内的文件

        # ============================================================
        # Step 2: 直接 docker exec 确认容器名
        # ============================================================
        import subprocess
        result = subprocess.run(
            ["docker", "ps", "--filter", "label=wisepen.role=aio-worker",
             "--format", "{{.Names}}", "--no-trunc"],
            capture_output=True, text=True,
        )
        workers = [w for w in result.stdout.strip().split("\n") if w]
        if not workers:
            print("FAIL: No AIO worker containers found. Start gateway with Docker available.")
            return
        worker = workers[0]
        print(f"  Using worker: {worker}")

        # ============================================================
        # Step 3: 直接操作同一容器（绕过 MCP，验证单容器一致性）
        # ============================================================
        CT_WORKSPACE = f"/home/gem/workspaces/{UID}/{SID}"  # Docker volume mount

        # 3a. 确保容器内工作目录存在
        print(f"\n[2] Creating workspace dir in container...")
        subprocess.run(["docker", "exec", worker, "mkdir", "-p", CT_WORKSPACE])

        # 3b. 写入文件
        print(f"\n[3] Writing file via docker exec...")
        subprocess.run(["docker", "exec", worker, "sh", "-c",
                        f"echo 'hello from single container' > {CT_WORKSPACE}/test.txt"])

        # 3c. 读取文件
        print(f"\n[4] Reading file via docker exec...")
        r = subprocess.run(["docker", "exec", worker, "cat",
                           f"{CT_WORKSPACE}/test.txt"],
                          capture_output=True, text=True)
        print(f"  Content: {r.stdout.strip()}")

        # 3d. 列出目录
        print(f"\n[5] Listing directory via docker exec...")
        r = subprocess.run(["docker", "exec", worker, "ls", "-la", CT_WORKSPACE],
                          capture_output=True, text=True)
        print(f"  Files:\n{r.stdout}")

        # 3e. 执行命令
        print(f"\n[6] Shell exec via docker exec...")
        r = subprocess.run(["docker", "exec", worker, "sh", "-c",
                           f"cat {CT_WORKSPACE}/test.txt | wc -c && echo '---' && python3 -c 'print(1+1)'"],
                          capture_output=True, text=True)
        print(f"  Output:\n{r.stdout.strip()}")

        # ============================================================
        # Step 4: 通过 Gateway REST API 操作（acquire→execute→release 每请求一次）
        # ============================================================
        print(f"\n{'=' * 60}")
        print("[7] Writing via Gateway REST API (same user/session)...")

        # Gateway 每请求都会 acquire/pull → execute → push/release
        # 由于我们修复了 file_manager 路径，本次写入后 push 会将文件存到主机缓存
        resp = await client.post(
            f"{base}/file/write", headers=HEADERS,
            json={"file": "/workspace/test.txt",
                  "content": "written via gateway REST API\nline 2",
                  "encoding": "utf-8"},
        )
        print(f"  Write: {resp.status_code} {resp.text[:200]}")

        # 再次读取（可能不同容器，但 pull 会恢复文件）
        print("\n[8] Reading via Gateway REST API...")
        resp = await client.post(
            f"{base}/file/read", headers=HEADERS,
            json={"file": "/workspace/test.txt"},
        )
        print(f"  Read: {resp.status_code} {resp.text[:300]}")

        # 列目录
        print("\n[9] List directory via Gateway REST API...")
        resp = await client.post(
            f"{base}/file/list", headers=HEADERS,
            json={"path": "/workspace", "recursive": False},
        )
        print(f"  List: {resp.status_code} {resp.text[:300]}")

        # Shell 执行
        print("\n[10] Shell exec via Gateway REST API...")
        resp = await client.post(
            f"{base}/shell/exec", headers=HEADERS,
            json={"command": "cat /home/gem/workspaces/u1/s1/test.txt",
                  "exec_dir": "/workspace"},
        )
        print(f"  Shell: {resp.status_code} {resp.text[:300]}")

        print("\n" + "=" * 60)
        print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
