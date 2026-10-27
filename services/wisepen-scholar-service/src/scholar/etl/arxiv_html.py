"""串行抓取 arXiv HTML，保存清洗后的正文与版本进度，再通过现有发布器写入 ES。"""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from time import monotonic

import duckdb
import httpx
import orjson

from scholar.etl.arxiv_html_cleaner import clean_arxiv_html


class HtmlStatus(StrEnum):
    PENDING = "pending"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


async def import_arxiv_html(
    db: duckdb.DuckDBPyConnection,
    http: httpx.AsyncClient,
    *,
    publish: Callable[[], Awaitable[dict]],
    batch_size: int = 20,
    request_interval: float = 3,
    max_batches: int | None = None,
) -> dict:
    """只请求 /html/{id}{version}；不支持则跳过，不尝试 PDF、ar5iv 或其他来源。

    正文、HTML 进度与待发布 ID 同事务保存；网络或 ES 中断时，重跑先补发已保存正文。
    同一版本的“不支持”也记录，避免每次恢复重复下载；新增版本会重新检查。
    """
    print(orjson.dumps({"stage": "arxiv_html_prepare"}).decode(), flush=True)
    db.execute("""CREATE TABLE IF NOT EXISTS arxiv_html (
        arxiv_id VARCHAR PRIMARY KEY, version VARCHAR NOT NULL,
        status VARCHAR NOT NULL CHECK(status IN ('pending','available','unavailable'))
    )""")
    # 只在本轮开始同步一次队列；抓取期间不反复扫描数百万篇论文的 JSON。
    db.execute("""INSERT INTO arxiv_html
        SELECT arxiv_id,coalesce(json_extract_string(document,'$.versions[#-1].version'),''),'pending'
        FROM arxiv
        ON CONFLICT(arxiv_id) DO UPDATE SET version=excluded.version,status='pending'
        WHERE arxiv_html.version <> excluded.version""")
    db.execute(
        "DELETE FROM arxiv_html WHERE arxiv_id NOT IN (SELECT arxiv_id FROM arxiv)"
    )
    counts = dict(
        db.execute("SELECT status,count(*) FROM arxiv_html GROUP BY status").fetchall()
    )
    available = counts.get(HtmlStatus.AVAILABLE, 0)
    skipped = counts.get(HtmlStatus.UNAVAILABLE, 0)
    total = sum(counts.values())
    await publish()
    print(
        orjson.dumps(
            {
                "stage": "arxiv_html",
                "total": total,
                "processed": available + skipped,
                "available": available,
                "skipped": skipped,
                "remaining": total - available - skipped,
            }
        ).decode(),
        flush=True,
    )
    batches = 0
    previous_request = -request_interval
    while max_batches is None or batches < max_batches:
        # 数字 ID 优先且由新到旧；老式分类 ID 同样会检查，不凭年份猜测 HTML 是否存在。
        rows = db.execute(
            """SELECT arxiv_id,version FROM arxiv_html WHERE status='pending'
            ORDER BY try_cast(substr(arxiv_id,1,4) AS INTEGER) DESC NULLS LAST,arxiv_id DESC
            LIMIT ?""",
            [batch_size],
        ).fetchall()
        if not rows:
            break
        for arxiv_id, version in rows:
            await asyncio.sleep(
                max(0, previous_request + request_interval - monotonic())
            )
            previous_request = monotonic()
            response = await http.get(f"https://arxiv.org/html/{arxiv_id}{version}")
            if response.status_code in (404, 410):
                cleaned = None
            else:
                # 限流、拒绝访问、服务故障和网络错误不是“不支持”，保持 pending 并直接报错。
                response.raise_for_status()
                cleaned = clean_arxiv_html(response.text)
            status = HtmlStatus.UNAVAILABLE if cleaned is None else HtmlStatus.AVAILABLE
            db.begin()
            try:
                if cleaned is not None:
                    # 只索引 body；标题和摘要已有权威元数据，不用 HTML 覆写，也不重复加权。
                    db.execute(
                        "UPDATE arxiv SET document=json_merge_patch(document,?::JSON) WHERE arxiv_id=?",
                        [
                            orjson.dumps({"full_text": cleaned["body"]}).decode(),
                            arxiv_id,
                        ],
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO pending_publish VALUES (?)", [arxiv_id]
                    )
                db.execute(
                    "UPDATE arxiv_html SET status=? WHERE arxiv_id=?",
                    [status.value, arxiv_id],
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
            available += status == HtmlStatus.AVAILABLE
            skipped += status == HtmlStatus.UNAVAILABLE
            # 看板只消费聚合计数；不支持的论文不输出逐篇警告或失败清单。
            print(
                orjson.dumps(
                    {
                        "stage": "arxiv_html",
                        "total": total,
                        "processed": available + skipped,
                        "available": available,
                        "skipped": skipped,
                        "remaining": total - available - skipped,
                    }
                ).decode(),
                flush=True,
            )
        await publish()
        batches += 1
    return {
        "stage": "arxiv_html_complete",
        "total": total,
        "processed": available + skipped,
        "available": available,
        "skipped": skipped,
        "remaining": total - available - skipped,
        "complete": available + skipped == total,
    }
