"""一次性离线迁移：冻结旧索引、异步 reindex、验收后切换 alias 与 DuckDB 写入目标。

整个运行持有 DuckDB 写连接，阻止其他进程启动 ETL；旧索引保留并保持只读。
断网后用 --resume 继续跟踪原 ES 任务；需要放弃迁移时用 --rollback 恢复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
from elastic_transport import ConnectionError, ConnectionTimeout
from redis.asyncio import Redis

from scholar.core.storage.elasticsearch.paper_repository import (
    ARXIV_MAPPING,
    ElasticsearchPaperRepository,
    create_elasticsearch,
)


def record(path: Path, state: dict, stage: str, **progress) -> None:
    """先落恢复记录，再发给看板；不记录连接地址或凭证。"""
    state.update(stage=stage, updated_at=datetime.now(UTC).isoformat(), **progress)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
    print(json.dumps(state, ensure_ascii=False), flush=True)


async def rollback(db, es, state: dict) -> None:
    """仅在尚未恢复 ETL 时回滚；跨库操作不伪称一个原子事务。"""
    old, new, alias = state["old_index"], state["new_index"], state["alias"]
    current = set(await es.indices.get_alias(name=alias))
    target = db.execute("SELECT target_index FROM import_state").fetchone()[0]
    if current not in ({old}, {new}) or target not in (old, new):
        raise RuntimeError(
            "migration ownership changed; refuse to overwrite a later publication"
        )
    # 取消尚在执行的后台复制；目标索引保留，便于定位真实失败。
    if state.get("task_id"):
        task = await es.tasks.get(task_id=state["task_id"])
        if not task["completed"]:
            await es.tasks.cancel(task_id=state["task_id"])
    if current == {new}:
        await es.indices.update_aliases(
            actions=[
                {"remove": {"index": new, "alias": alias}},
                {"add": {"index": old, "alias": alias}},
            ]
        )
    db.execute("UPDATE import_state SET target_index=?", [old])
    await es.indices.put_settings(index=old, settings={"index.blocks.write": False})


async def migrate(
    db,
    es,
    redis,
    alias: str,
    journal: Path,
    *,
    poll_seconds: float = 15,
    resume: bool = False,
) -> dict:
    """复制期间服务仍可读旧索引；全部验收通过才执行短暂的双目标切换。"""
    if journal.exists() and not resume:
        raise FileExistsError(
            f"journal already exists; use --resume or --rollback: {journal}"
        )
    row = db.execute(
        "SELECT complete,published,target_index FROM import_state"
    ).fetchone()
    pending = db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
    deletes = db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]
    if not row or not row[0] or not row[1] or pending or deletes:
        raise RuntimeError(
            "migration requires a fully published baseline with no pending writes"
        )
    old = row[2]
    if set(await es.indices.get_alias(name=alias)) != {old}:
        raise RuntimeError("ES alias and DuckDB write target disagree")
    await redis.ping()
    original = (await es.indices.get_settings(index=old, flat_settings=True))[old][
        "settings"
    ]
    if resume:
        state = json.loads(journal.read_text(encoding="utf-8"))
        if state["alias"] != alias or state["old_index"] != old:
            raise RuntimeError(
                "resume requires the original alias and offline write target; use --rollback for a partial cutover"
            )
        # 写屏障持续存在，才能证明断网期间复制源没有继续变化。
        if original.get("index.blocks.write", "false") != "true":
            raise RuntimeError(
                "source is no longer frozen; cannot reuse the old copy safely"
            )
        if not state.get("task_id"):
            raise RuntimeError(
                "no recorded ES task to resume; inspect the preparation failure"
            )
        state.pop("error", None)
        state.pop("committed", None)
    else:
        if original.get("index.blocks.write", "false") == "true":
            raise RuntimeError(
                "source index is already write-blocked; resolve the previous operation first"
            )
        state = {
            "alias": alias,
            "old_index": old,
            "new_index": f"{alias}-{uuid4().hex}",
        }
        record(journal, state, "migration_prepare")
    try:
        # 恢复时重新取得本地写锁并核对冻结源，复用原任务，不重建或覆盖目标索引。
        if not resume:
            await es.indices.add_block(index=old, block="write")
        await es.indices.refresh(index=old)
        total = db.execute("SELECT count(*) FROM arxiv").fetchone()[0]
        if (await es.count(index=old))["count"] != total:
            raise RuntimeError(
                "source index count differs from the offline paper database"
            )
        new = state["new_index"]
        if not resume:
            await es.indices.create(
                index=new,
                mappings=ARXIV_MAPPING,
                settings={
                    "number_of_shards": original["index.number_of_shards"],
                    "number_of_replicas": 0,
                    "refresh_interval": "-1",
                },
            )
            response = await es.reindex(
                source={"index": old, "size": 1000},
                dest={"index": new, "op_type": "create"},
                wait_for_completion=False,
                slices=1,
            )
            state["task_id"] = response["task"]
        record(
            journal,
            state,
            "migration_copy",
            total=total,
            written=state.get("written", 0),
        )
        while True:
            task = await es.tasks.get(task_id=state["task_id"])
            if task["completed"]:
                if "error" in task:
                    raise RuntimeError(f"reindex failed: {task['error']}")
                result = task["response"]
                if (
                    result["failures"]
                    or result["created"] != total
                    or result["version_conflicts"]
                    or result["timed_out"]
                ):
                    raise RuntimeError(
                        f"reindex did not copy the complete baseline: {result}"
                    )
                break
            status = task["task"]["status"]
            record(
                journal, state, "migration_copy", written=status["created"], total=total
            )
            await asyncio.sleep(poll_seconds)

        record(journal, state, "migration_verify", written=total)
        await es.indices.put_settings(
            index=new,
            settings={
                "refresh_interval": original.get("index.refresh_interval", "1s"),
                "number_of_replicas": original["index.number_of_replicas"],
            },
        )
        await es.indices.refresh(index=new)
        health = await es.cluster.health(
            index=new, wait_for_status="yellow", timeout="120s"
        )
        if health["timed_out"]:
            raise RuntimeError("new index primary shards did not become available")
        if (await es.count(index=new))["count"] != total:
            raise RuntimeError(
                "new index count differs from the offline paper database"
            )
        # 检查附加事实完整性；reindex 的成功项数及统一 mapping 证明每个源文档均重新分析。
        facts = {}
        for field in ("metadata.s2.corpus_id", "metadata.s2.tldr.text"):
            query = {"exists": {"field": field}}
            before = (await es.count(index=old, query=query))["count"]
            after = (await es.count(index=new, query=query))["count"]
            if before != after:
                raise RuntimeError(
                    f"metadata coverage changed: {field}: {before} -> {after}"
                )
            facts[field] = after
        sample = await es.search(
            index=old, size=3, sort=["arxiv_id"], query={"match_all": {}}
        )
        hits = sample["hits"]["hits"]
        copied = await es.mget(index=new, ids=[hit["_id"] for hit in hits])
        for hit, item in zip(hits, copied["docs"], strict=True):
            if not item["found"] or item["_source"] != hit["_source"]:
                raise RuntimeError("sample paper facts changed during reindex")
        # 使用真正的查询入口验证新字段可用，不把 count 成功等同于检索成功。
        repo = ElasticsearchPaperRepository(es, new)
        papers = await repo.search(
            query="language models",
            categories=[],
            published_after=None,
            published_before=None,
            limit=3,
        )
        if not papers:
            raise RuntimeError("new BM25F search returned no language-model papers")
        record(
            journal,
            state,
            "migration_switch",
            facts=facts,
            sample_ids=[hit["_id"] for hit in hits],
            search_titles=[p.title for p in papers],
        )
        # DuckDB 写锁在整个过程中保持；异常时用 journal 恢复两端，旧索引从不删除。
        await es.indices.update_aliases(
            actions=[
                {"remove": {"index": old, "alias": alias}},
                {"add": {"index": new, "alias": alias}},
            ]
        )
        db.execute("UPDATE import_state SET target_index=?", [new])
        removed = 0
        async for key in redis.scan_iter(match=f"scholar:search:{alias}:*", count=500):
            removed += await redis.unlink(key)
        if set(await es.indices.get_alias(name=alias)) != {new}:
            raise RuntimeError("alias verification failed after cutover")
        if db.execute("SELECT target_index FROM import_state").fetchone()[0] != new:
            raise RuntimeError("offline write target verification failed after cutover")
        record(
            journal,
            state,
            "migration_complete",
            invalidated_cache_keys=removed,
            committed=True,
        )
        return state
    except BaseException as failure:
        # 复制/验收期断网并不代表 ES 任务失败；保留写屏障和任务，避免误取消后台成果。
        if isinstance(failure, (ConnectionError, ConnectionTimeout)) and state[
            "stage"
        ] in ("migration_prepare", "migration_copy", "migration_verify"):
            record(journal, state, "migration_paused", error=str(failure))
            raise
        # 校验或切换失败仍回滚；恢复也失败时同时报告，禁止伪称恢复完成。
        record(journal, state, "migration_rollback", error=str(failure))
        try:
            await rollback(db, es, state)
        except BaseException as recovery_failure:  # noqa: BLE001 - 与原失败一起重新抛出。
            raise BaseExceptionGroup(
                "migration and recovery failed; keep ETL stopped",
                [
                    failure,
                    recovery_failure,
                ],
            ) from None
        record(journal, state, "migration_rolled_back", committed=False)
        raise


async def main() -> None:
    from scholar.core.config.app_settings import settings
    from scholar.core.config.bootstrap_settings import SERVICE_ROOT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, default=SERVICE_ROOT / ".runtime/scholar.duckdb"
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=SERVICE_ROOT / ".runtime/search-index-migration.json",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--rollback", action="store_true")
    action.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    # 不创建空库或修改 schema；连接已有数据库的写锁也是本次迁移的离线互斥边界。
    if not args.db.is_file():
        raise FileNotFoundError(args.db)
    with duckdb.connect(str(args.db)) as db:
        db.execute("SET memory_limit='1GB'")
        db.execute("SET threads=2")
        async with (
            create_elasticsearch(
                settings.ELASTICSEARCH_URIS,
                settings.ELASTICSEARCH_USERNAME,
                settings.ELASTICSEARCH_PASSWORD,
                180,
            ) as es,
            Redis.from_url(settings.REDIS_URL) as redis,
        ):
            if args.rollback:
                state = json.loads(args.journal.read_text(encoding="utf-8"))
                await rollback(db, es, state)
                record(args.journal, state, "migration_rolled_back", committed=False)
            else:
                await migrate(
                    db,
                    es,
                    redis,
                    settings.SCHOLAR_INDEX_ALIAS,
                    args.journal,
                    resume=args.resume,
                )


if __name__ == "__main__":
    asyncio.run(main())
