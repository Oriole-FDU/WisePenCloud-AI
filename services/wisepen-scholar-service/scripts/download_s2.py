import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

# ==================== 官方核心配置 ====================
# 从 Scholar 服务目录读取配置，不依赖调用脚本时的工作目录。
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
API_KEY = os.environ["S2_API_KEY"]
RELEASE_ID = "2026-10-20"  # 指定官方发布的快照日期
DATASETS = ["papers", "tldrs"]  # 同时同步的两个核心包
SAVE_DIR = "./s2_download"

# 1. 频控与并发设置
MAX_WORKERS = 5  # 切片下载并发数（建议 2-4）
MAX_RETRIES = 6  # 偶发断流最大重试次数
CHUNK_SIZE = 1024 * 1024  # 1MB 读写缓冲区
API_DELAY_SECONDS = 1.0  # 官方 API 接口间隔冷却

# ====================================================

# 官方规范接口根路径
BASE_URL = "https://api.semanticscholar.org/datasets/v1/release"

COMMON_UA = "curl/8.21.0"
API_HEADERS = {
    "x-api-key": API_KEY,
}

api_session = requests.Session()
# requests.Session 默认添加 User-Agent；Scholar 的 S2 API 约定要求省略该请求头。
api_session.headers.pop("User-Agent")

os.makedirs(SAVE_DIR, exist_ok=True)

# 终端进度条槽位调度锁
position_lock = threading.Lock()
available_positions = list(range(MAX_WORKERS))


def acquire_position():
    with position_lock:
        return available_positions.pop(0)


def release_position(pos):
    with position_lock:
        available_positions.append(pos)
        available_positions.sort()


def fetch_dataset_files(dataset_name: str, release_id: str) -> list[str]:
    """完全遵循官方示例：获取指定 Release 下指定数据集的 S3 预签名直链清单"""
    time.sleep(API_DELAY_SECONDS)
    target_url = f"{BASE_URL}/{release_id}/dataset/{dataset_name}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = api_session.get(
                target_url,
                headers=API_HEADERS,
                timeout=30,
            )
            if res.status_code == 429:
                wait_time = int(res.headers.get("Retry-After", 5 * attempt)) + random.uniform(0.5, 1.5)
                print(f"\n[429 频控] 获取 {dataset_name} 元数据受限，冷却 {wait_time:.1f} 秒...")
                time.sleep(wait_time)
                continue

            res.raise_for_status()
            data = res.json()
            return data.get("files", [])

        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 * attempt)
    return None


def download_chunk_with_resume(url: str, save_path: str, desc: str):
    """基于 HTTP Range 请求的 S3 分片流式断点续传下载"""
    pos = acquire_position()
    try:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                existing_size = os.path.getsize(save_path) if os.path.exists(save_path) else 0

                # S3 预签名直链必须剔除 x-api-key，仅带 Range 和 UA
                req_headers = {"User-Agent": COMMON_UA}
                if existing_size > 0:
                    req_headers["Range"] = f"bytes={existing_size}-"

                with requests.get(
                        url,
                        headers=req_headers,
                        stream=True,
                        timeout=60,
                ) as response:
                    # 416 表示请求的字节范围已超出文件大小，即本地切片已完整
                    if response.status_code == 416:
                        tqdm.write(f"[{desc}] 文件校验通过，已为完整切片。")
                        return

                    if response.status_code == 429:
                        wait_time = int(response.headers.get("Retry-After", 10 * attempt))
                        time.sleep(wait_time)
                        continue

                    response.raise_for_status()

                    # 206 代表命中断点续传追加，200 代表新任务覆盖重写
                    if response.status_code == 206:
                        content_len = int(response.headers.get("content-length", 0))
                        total_size = existing_size + content_len
                        mode = "ab"
                    else:
                        total_size = int(response.headers.get("content-length", 0))
                        existing_size = 0
                        mode = "wb"

                    if 0 < total_size <= existing_size:
                        return

                    with open(save_path, mode) as f, tqdm(
                            total=total_size,
                            initial=existing_size,
                            unit="B",
                            unit_scale=True,
                            unit_divisor=1024,
                            desc=desc,
                            position=pos,
                            leave=False,
                    ) as pbar:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                pbar.update(len(chunk))
                return

            except (requests.exceptions.RequestException, IOError) as err:
                if attempt == MAX_RETRIES:
                    tqdm.write(f"[{desc}] 下载多次重试失败: {err}")
                    raise
                time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
    finally:
        release_position(pos)


def download_s2_datasets():
    print(f"目标快照版本: {RELEASE_ID}")
    tasks = []

    # 1. 顺序读取两个包的分片链接
    for dataset_name in DATASETS:
        dataset_dir = os.path.join(SAVE_DIR, dataset_name)
        os.makedirs(dataset_dir, exist_ok=True)

        print(f"正在读取【{dataset_name}】切片列表...")
        files = fetch_dataset_files(dataset_name, RELEASE_ID)
        print(f"【{dataset_name}】共计 {len(files)} 个切片。")

        for index, url in enumerate(files):
            file_name = f"{dataset_name}_part_{index:04d}.json.gz"
            file_path = os.path.join(dataset_dir, file_name)
            desc = f"{dataset_name}#{index:02d}"
            tasks.append((url, file_path, desc))

    print(f"\n任务汇总完成，共有 {len(tasks)} 个切片等待下载。")
    print(f"并发数: {MAX_WORKERS} | 断点续传已开启\n")

    # 2. 线程池并发下载并维持断点续传
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_task = {
            executor.submit(download_chunk_with_resume, url, path, desc): desc
            for url, path, desc in tasks
        }

        for future in as_completed(future_to_task):
            desc = future_to_task[future]
            try:
                future.result()
            except Exception as exc:
                print(f"\n分片任务 [{desc}] 异常中断: {exc}")

    print("\n所有切片处理完成。")


if __name__ == "__main__":
    download_s2_datasets()
