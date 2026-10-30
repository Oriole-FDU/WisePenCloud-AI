#!/usr/bin/env python3
"""S2 Graph API 降级兜底压测脚本 (双队列独立节流，串行，异步版)。

架构：
    NOKEY 队列：独立消费者 + 独立 interval（以上一次 NOKEY 发出时刻为锚点节流）
    KEY   队列：独立消费者 + 独立 interval（以上一次 KEY   发出时刻为锚点节流）
    一次调用：
        投递到 NOKEY 队列 → 等 NOKEY 返回；
        200 → 调用成功，结束（不进 KEY 队列）；
        429 → 投递到 KEY 队列 → 等 KEY 返回；
                200 → 调用成功；非200 → 调用失败。
    两个队列的 last_launch_ts 完全独立，KEY 请求的发射只受 KEY 队列自身节奏约束。
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    load_dotenv(env_path)

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = (
    "paperId,corpusId,externalIds,url,title,tldr,"
    "citationCount,influentialCitationCount,openAccessPdf,embedding.specter_v2"
)

STOP = object()


def load_sample_arxiv_ids(db_path: Path, count: int = 300) -> list[str]:
    return [f"ARXIV:0704.{i:04d}" for i in range(1, count + 1)]


def resolve_proxy(proxy_port: int | None = None) -> str | None:
    if proxy_port is not None:
        return f"http://127.0.0.1:{proxy_port}"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    return None


async def send_leg(client: httpx.AsyncClient, headers: dict, paper_ids: list[str]) -> dict:
    t_start = time.perf_counter()
    status = None
    matched = 0
    retry_after = None
    desc = ""
    try:
        resp = await client.post(
            S2_BATCH_URL,
            params={"fields": S2_FIELDS},
            json={"ids": paper_ids},
            headers=headers,
        )
        latency = (time.perf_counter() - t_start) * 1000.0
        status = resp.status_code
        retry_after = resp.headers.get("retry-after")
        if status == 200:
            data = resp.json()
            if isinstance(data, list):
                matched = sum(1 for x in data if x is not None)
            desc = f"成功 (匹配 {matched}/{len(paper_ids)} 篇)"
        elif status == 429:
            ra = f", Retry-After: {retry_after}s" if retry_after else ""
            desc = f"429 限流 ({resp.text[:80].strip()}{ra})"
        elif status == 400:
            desc = f"400 Bad Request ({resp.text[:80].strip()})"
        else:
            desc = f"HTTP {status} ({resp.text[:80].strip()})"
    except httpx.RequestError as e:
        latency = (time.perf_counter() - t_start) * 1000.0
        status = 0
        desc = f"网络异常 ({type(e).__name__}: {e})"

    return {
        "status": status,
        "latency_ms": latency,
        "matched": matched,
        "retry_after": retry_after,
        "desc": desc,
    }


class PacedQueue:
    """带独立节流锚点的模拟队列 + 单消费者协程。

    消费者循环：
        item = await queue.get()
        等到距【本队列上一次发出】>= interval
        记录新的发射时刻，create_task 发请求，立即回去取下一条
    两个队列各自持有 last_launch_ts 与 interval，互不影响。
    """

    def __init__(
        self,
        name: str,
        interval: float,
        client: httpx.AsyncClient,
        headers: dict,
        paper_ids: list[str],
        t0: float,
    ):
        self.name = name
        self.interval = interval
        self.client = client
        self.headers = headers
        self.paper_ids = paper_ids
        self.t0 = t0
        self.queue: asyncio.Queue = asyncio.Queue()
        self.last_launch_ts: float | None = None
        self.launch_gaps: list[float] = []
        self.launch_count = 0
        self._consumer_task: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()

    def start(self) -> None:
        self._consumer_task = asyncio.create_task(self._consumer())

    async def put(self, round_idx: int) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((round_idx, fut))
        return fut

    async def stop(self) -> None:
        await self.queue.put(STOP)
        if self._consumer_task:
            await self._consumer_task
        if self._inflight:
            await asyncio.gather(*self._inflight)

    async def _consumer(self) -> None:
        while True:
            item = await self.queue.get()
            if item is STOP:
                break
            round_idx, fut = item

            now = time.perf_counter()
            if self.last_launch_ts is None:
                gap = None
                wait_s = 0.0
            else:
                gap = now - self.last_launch_ts
                wait_s = max(0.0, self.interval - gap)

            print(
                f"[QUEUE] #{round_idx:02d} [{self.name}] | "
                f"距上次[{self.name}]发出: {'--' if gap is None else f'{gap:.3f}s'} | "
                f"需等待 {wait_s:.3f}s",
                flush=True,
            )
            if wait_s > 0:
                await asyncio.sleep(wait_s)

            launch_ts = time.perf_counter()
            if self.last_launch_ts is not None:
                self.launch_gaps.append(launch_ts - self.last_launch_ts)
            self.last_launch_ts = launch_ts
            self.launch_count += 1

            print(
                f"[SEND ] #{round_idx:02d} [{self.name}] | T+{launch_ts - self.t0:6.2f}s | "
                f"发出 ({len(self.paper_ids)} 篇)...",
                flush=True,
            )
            t = asyncio.create_task(self._do_request(round_idx, fut, launch_ts))
            self._inflight.add(t)
            t.add_done_callback(self._inflight.discard)

    async def _do_request(
        self, round_idx: int, fut: asyncio.Future, launch_ts: float
    ) -> None:
        leg = await send_leg(self.client, self.headers, self.paper_ids)
        recv_s = time.perf_counter() - self.t0
        print(
            f"[RECV ] #{round_idx:02d} [{self.name}] | T+{recv_s:6.2f}s | "
            f"耗时: {leg['latency_ms']:7.1f}ms | 状态: {leg['status'] or 'ERR'} | {leg['desc']}",
            flush=True,
        )
        leg["launch_s"] = launch_ts - self.t0
        leg["recv_s"] = recv_s
        if not fut.done():
            fut.set_result(leg)


async def do_call(
    round_idx: int,
    nokey_q: PacedQueue,
    key_q: PacedQueue,
    t0: float,
) -> dict:
    """一次调用 = NOKEY 首发；仅 429 后投递到 KEY 队列兜底。"""
    # 第一段：NOKEY 首发（由 NOKEY 队列独立节流）
    nokey_fut = await nokey_q.put(round_idx)
    leg_nokey = await nokey_fut

    if leg_nokey["status"] == 200:
        print(
            f"[CALL ] #{round_idx:02d} [OK ] | T+{time.perf_counter() - t0:6.2f}s | "
            f"调用成功 (胜出: NOKEY, 未降级)",
            flush=True,
        )
        return {
            "round": round_idx,
            "ok": True,
            "used_fallback": False,
            "winner": "NOKEY",
            "leg_nokey": leg_nokey,
            "leg_key": None,
            "call_end_s": time.perf_counter() - t0,
        }

    # 是否降级
    need_fallback = leg_nokey["status"] == 429
    if not need_fallback:
        print(
            f"[CALL ] #{round_idx:02d} [FAIL] | T+{time.perf_counter() - t0:6.2f}s | "
            f"NOKEY 非 429 错误，直接失败",
            flush=True,
        )
        return {
            "round": round_idx,
            "ok": False,
            "used_fallback": False,
            "winner": None,
            "leg_nokey": leg_nokey,
            "leg_key": None,
            "call_end_s": time.perf_counter() - t0,
        }

    # 第二段：降级到 KEY 队列（由 KEY 队列独立节流）
    print(
        f"[FALLB] #{round_idx:02d} | T+{time.perf_counter() - t0:6.2f}s | "
        f"NOKEY 失败 (状态 {leg_nokey['status'] or 'ERR'})，投递到 KEY 队列兜底...",
        flush=True,
    )
    key_fut = await key_q.put(round_idx)
    leg_key = await key_fut

    ok = leg_key["status"] == 200
    tag = "[OK ]" if ok else "[FAIL]"
    tail = (
        f"调用成功 (胜出: KEY, 走了降级)"
        if ok
        else "调用失败 (NOKEY 与 KEY 均未成功)"
    )
    print(
        f"[CALL ] #{round_idx:02d} {tag} | T+{time.perf_counter() - t0:6.2f}s | {tail}",
        flush=True,
    )
    return {
        "round": round_idx,
        "ok": ok,
        "used_fallback": True,
        "winner": "KEY" if ok else None,
        "leg_nokey": leg_nokey,
        "leg_key": leg_key,
        "call_end_s": time.perf_counter() - t0,
    }


async def run_stress_test(
    nokey_interval: float,
    key_interval: float,
    rounds: int,
    batch_size: int,
    proxy_port: int | None,
    api_key: str | None,
    db_path: Path,
):
    proxy_url = resolve_proxy(proxy_port)
    key = api_key or os.environ.get("S2_API_KEY", "")

    print("=" * 80, flush=True)
    print("S2 Graph Batch API 降级兜底压测 (双队列独立节流，串行，异步)", flush=True)
    print("=" * 80, flush=True)
    print(f"每批请求数量       : {batch_size} 篇", flush=True)
    print(f"连续调用轮数       : {rounds} 次", flush=True)
    print(f"NOKEY 队列 interval: {nokey_interval} 秒 (以上一次 NOKEY 发出为锚点)", flush=True)
    print(f"KEY   队列 interval: {key_interval} 秒 (以上一次 KEY   发出为锚点)", flush=True)
    print(f"两队列节流         : 完全独立，KEY 请求不再被 NOKEY 节奏带动，也不跟随 NOKEY 爆发", flush=True)
    print("降级触发条件       : 仅 NOKEY HTTP 429", flush=True)
    print(f"网络代理设置       : {proxy_url or '直连 (无代理)'}", flush=True)
    print(f"S2 API Key        : {'已配置 (前缀: ' + key[:6] + '***)' if key else '未配置 (降级无兜底)'}", flush=True)
    print("=" * 80, flush=True)

    paper_ids = load_sample_arxiv_ids(db_path, count=batch_size)
    print(f"已就绪 {len(paper_ids)} 个测试 ID (示例: {paper_ids[0]} ... {paper_ids[-1]})\n", flush=True)

    base_headers = {
        "Accept": "application/json",
    }
    headers_key = dict(base_headers)
    if key:
        headers_key["x-api-key"] = key
    headers_nokey = dict(base_headers)

    t0 = time.perf_counter()

    limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)
    async with httpx.AsyncClient(proxy=proxy_url, timeout=60.0, limits=limits) as client:
        nokey_q = PacedQueue("NOKEY", nokey_interval, client, headers_nokey, paper_ids, t0)
        key_q = PacedQueue("KEY  ", key_interval, client, headers_key, paper_ids, t0)
        nokey_q.start()
        key_q.start()

        call_tasks = [
            asyncio.create_task(do_call(i, nokey_q, key_q, t0))
            for i in range(1, rounds + 1)
        ]
        results = list(await asyncio.gather(*call_tasks))

        await nokey_q.stop()
        await key_q.stop()

    total_elapsed = time.perf_counter() - t0
    results.sort(key=lambda r: r["round"])

    # ---- 统计报表 ----
    total = len(results)
    ok_calls = sum(1 for r in results if r["ok"])
    fallback_cnt = sum(1 for r in results if r["used_fallback"])
    fallback_ok = sum(1 for r in results if r["used_fallback"] and r["ok"])
    fallback_fail = fallback_cnt - fallback_ok
    nokey_ok = sum(1 for r in results if r["leg_nokey"]["status"] == 200)
    both_fail = total - ok_calls
    nokey_hit_rate = nokey_ok / total if total else 0.0
    fallback_success_rate = fallback_ok / fallback_cnt if fallback_cnt else 0.0

    def pct(x: int, d: int = total) -> str:
        return f"{x / d * 100:6.2f}%" if d else "   --  "

    print("\n" + "=" * 80, flush=True)
    print("降级兜底压测统计汇总 (双队列独立节流)", flush=True)
    print("=" * 80, flush=True)
    print(f"总调度测试时长        : {total_elapsed:.2f} 秒", flush=True)
    print(f"总调用次数            : {total}", flush=True)
    print(f"NOKEY 实际发出请求数  : {nokey_q.launch_count}  (首发必发，受 NOKEY interval 节流)", flush=True)
    print(f"KEY   实际发出请求数  : {key_q.launch_count}  (仅降级触发，受 KEY interval 节流)", flush=True)
    print("-" * 80, flush=True)
    print(f"调用成功数 (任一路成功) : {ok_calls:4d}  ({pct(ok_calls)})", flush=True)
    print(f"调用失败数 (两路都失败) : {both_fail:4d}  ({pct(both_fail)})", flush=True)
    print("-" * 80, flush=True)
    print(f"NO KEY 首发成功 (拦截)  : {nokey_ok:4d}  ({pct(nokey_ok)})", flush=True)
    print(f"NO KEY 失败进入降级     : {fallback_cnt:4d}  ({pct(fallback_cnt)})", flush=True)
    print(f"  ↳ KEY 兜底成功        : {fallback_ok:4d}  ({pct(fallback_ok, fallback_cnt)} of 降级数)", flush=True)
    print(f"  ↳ KEY 兜底也失败      : {fallback_fail:4d}  ({pct(fallback_fail, fallback_cnt)} of 降级数)", flush=True)
    print("-" * 80, flush=True)
    print(f"NO KEY 独立成功率       : {nokey_hit_rate * 100:6.2f}%", flush=True)
    print(f"降级兜底后整体成功率    : {(ok_calls / total * 100) if total else 0:6.2f}%", flush=True)
    if total:
        gain = ok_calls / total * 100 - nokey_hit_rate * 100
        print(f"相对纯 NOKEY 的提升     : {gain:+.2f} 个百分点", flush=True)
        key_save = (1 - fallback_cnt / total) * 100
        print(f"KEY 配额节省率          : {key_save:.2f}%  (省掉 {total - fallback_cnt}/{total} 次 KEY 调用)", flush=True)
    if fallback_cnt:
        print(
            f"降级兜底有效率          : {fallback_success_rate * 100:6.2f}%  "
            f"({'健康' if fallback_success_rate > 0.8 else '偏低，KEY 队列自身也可能被压满'})",
            flush=True,
        )

    lat_nokey = [r["leg_nokey"]["latency_ms"] for r in results if r["leg_nokey"]["status"] == 200]
    lat_key = [r["leg_key"]["latency_ms"] for r in results if r["leg_key"] and r["leg_key"]["status"] == 200]
    for name, lat in (("NOKEY 成功耗时", lat_nokey), ("KEY   兜底耗时", lat_key)):
        if lat:
            print(
                f"{name}  : 最小 {min(lat):.1f}ms | 平均 {sum(lat)/len(lat):.1f}ms | 最大 {max(lat):.1f}ms",
                flush=True,
            )

    # 双队列各自的发射间隔，分开看才能确认"两个 interval 独立生效"
    print("-" * 80, flush=True)
    for q in (nokey_q, key_q):
        if q.launch_gaps:
            g_min = min(q.launch_gaps)
            g_max = max(q.launch_gaps)
            g_avg = sum(q.launch_gaps) / len(q.launch_gaps)
            late_cnt = sum(1 for g in q.launch_gaps if g > q.interval + 0.05)
            print(
                f"[{q.name}] 实际发射间隔 : 最小 {g_min:.3f}s | 平均 {g_avg:.3f}s | 最大 {g_max:.3f}s "
                f"(目标 >= {q.interval:.3f}s) | 延迟发射 {late_cnt}/{len(q.launch_gaps)}",
                flush=True,
            )
    print("=" * 80, flush=True)


async def run_production_stress(
    nokey_interval, key_interval, rounds, batch_size, proxy_port, api_key, db_path,
):
    """使用生产协调器；两个客户端只通过 Redis 合流及分发，不使用模拟队列。"""
    from uuid import uuid4
    import re

    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    from scholar.core.config.app_settings import settings
    from scholar.core.providers.s2 import S2Transport
    from scholar.domain.errors import ProviderError

    key = api_key or os.environ.get("S2_API_KEY") or settings.S2_API_KEY
    redis_clients = [Redis.from_url(settings.REDIS_URL) for _ in range(2)]
    namespace = f"scholar:stress:s2:{uuid4().hex}"
    launches = {"NOKEY": [], "KEY": []}
    sent_ids = 0

    async def on_request(request):
        nonlocal sent_ids
        import json

        channel = "KEY" if "x-api-key" in request.headers else "NOKEY"
        launches[channel].append(time.perf_counter())
        sent_ids += len(json.loads(request.content)["ids"])

    budget = max(60, rounds * max(nokey_interval, key_interval) * 2 + 60)
    async with httpx.AsyncClient(
        proxy=resolve_proxy(proxy_port), timeout=60, event_hooks={"request": [on_request]},
    ) as http:
        clients = [S2Transport(
            http, redis, key, namespace=namespace, nokey_interval=nokey_interval,
            key_interval=key_interval, batch_max_ids=settings.SCHOLAR_S2_BATCH_MAX_IDS,
            max_queued_ahead=None, request_timeout=budget,
        ) for redis in redis_clients]
        # 相邻调用部分重叠，直接检查各调用收到的结果长度及位置对应关系。
        pool = load_sample_arxiv_ids(db_path, count=min(max(300, rounds * batch_size), 9999))

        async def call(index):
            ids = [pool[(index * max(1, batch_size // 2) + offset) % len(pool)]
                   for offset in range(batch_size)]
            try:
                values = await clients[index % 2].request(
                    "POST", "graph/v1/paper/batch", params={"fields": S2_FIELDS}, json={"ids": ids},
                )
            except (ProviderError, httpx.HTTPError, RedisError, TimeoutError) as error:
                print(f"[FAIL] #{index + 1}: {error}", flush=True)
                return False
            assert len(values) == len(ids)
            for identifier, value in zip(ids, values, strict=True):
                if value is not None:
                    actual = value.get("externalIds", {}).get("ArXiv")
                    if actual is not None:
                        assert re.sub(r"v[0-9]+$", "", actual) == identifier.split(":", 1)[1]
            return True

        try:
            results = await asyncio.gather(*(call(index) for index in range(rounds)))
        finally:
            try:
                await asyncio.gather(*(client.aclose() for client in clients))
            finally:
                keys = [item async for item in redis_clients[0].scan_iter(match=f"{namespace}:*")]
                if keys:
                    await redis_clients[0].delete(*keys)
                await asyncio.gather(*(redis.aclose() for redis in redis_clients))
    print(f"生产协调器：业务调用 {rounds}，成功 {sum(results)}，失败 {rounds - sum(results)}")
    print(f"提交 ID 数 {rounds * batch_size}，上游实际 ID 数（含兜底）{sent_ids}")
    for channel, timestamps in launches.items():
        gaps = [after - before for before, after in zip(timestamps, timestamps[1:])]
        print(f"{channel} 请求数 {len(timestamps)}，最小发射间隔 {min(gaps) if gaps else '--'}")


def main():
    parser = argparse.ArgumentParser(
        description="S2 批量接口降级兜底压测 (双队列独立节流，串行，异步)"
    )
    parser.add_argument(
        "--nokey-interval",
        type=float,
        default=1.5,
        help="NOKEY 队列内部的发射间隔（秒），默认 1.5",
    )
    parser.add_argument(
        "--key-interval",
        type=float,
        default=1.5,
        help="KEY   队列内部的发射间隔（秒），默认 1.5",
    )
    parser.add_argument("--rounds", type=int, default=10, help="连续调用总轮数，默认 10")
    parser.add_argument("--batch-size", type=int, default=300, help="单次批量请求的论文数量，默认 300")
    parser.add_argument("--proxy-port", type=int, default=None, help="代理端口（例如 7897），默认从系统环境变量读取")
    parser.add_argument("--api-key", type=str, default=None, help="显式指定 S2 API Key，默认从 .env 或环境变量 S2_API_KEY 读取")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".runtime" / "scholar.duckdb",
        help="DuckDB 本地数据库路径（保留参数，与生产 CLI 兼容）",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="使用生产 Redis 合流协调器和两个独立客户端验证合流与结果分发",
    )
    args = parser.parse_args()
    runner = run_production_stress if args.production else run_stress_test
    asyncio.run(
        runner(
            nokey_interval=args.nokey_interval,
            key_interval=args.key_interval,
            rounds=args.rounds,
            batch_size=args.batch_size,
            proxy_port=args.proxy_port,
            api_key=args.api_key,
            db_path=args.db,
        )
    )


if __name__ == "__main__":
    main()
