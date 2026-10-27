"""独立 ETL 命令：HTTP 生命周期不创建数据库写者或后台导入任务。"""

import argparse
import asyncio

import httpx
import orjson
from redis.asyncio import Redis

from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from scholar.core.s2_transport import S2Transport
from scholar.core.storage.elasticsearch.paper_repository import create_elasticsearch
from scholar.core.storage.redis.paper_cache import RedisPaperEnrichmentCache
from scholar.etl.arxiv import database_status, import_arxiv, open_database
from scholar.etl.arxiv_oai import ArxivOaiHarvester
from scholar.etl.arxiv_sync import sync_arxiv
from scholar.etl.publish import publish_arxiv
from scholar.etl.s2 import apply_s2_projection, import_s2
from scholar.etl.s2_datasets import S2DatasetsClient
from scholar.etl.s2_diff import sync_s2_diffs
from scholar.providers.s2 import S2Provider


async def run(args):
    with open_database(args.db) as db:
        if args.command == "import-arxiv":
            return import_arxiv(
                db,
                args.source,
                batch_size=args.batch_size,
                max_batches=args.max_batches,
            )
        if args.command == "status":
            return database_status(db)
        if args.command == "import-s2":
            status = import_s2(
                db,
                args.papers_dir,
                args.tldrs_dir,
                release_id=args.release_id,
                tldr_model=args.tldr_model,
                batch_size=args.batch_size,
                max_shards=args.max_shards,
            )
            # 不完整输入只记录扫描进度；不能把已读的部分当作整个 release 投影。
            projection = (
                apply_s2_projection(db, args.release_id) if status["complete"] else None
            )
            return {
                "s2": status,
                "projection": projection,
                "arxiv": database_status(db),
            }
        from scholar.core.config.app_settings import settings

        if args.command == "publish":
            async with create_elasticsearch(
                settings.ELASTICSEARCH_URIS,
                settings.ELASTICSEARCH_USERNAME,
                settings.ELASTICSEARCH_PASSWORD,
                settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
            ) as es:
                return await publish_arxiv(
                    db,
                    es,
                    args.alias or settings.SCHOLAR_INDEX_ALIAS,
                    batch_size=args.batch_size,
                    max_batches=args.max_batches,
                )

        if args.command == "sync-arxiv":
            state = db.execute("SELECT complete,published FROM import_state").fetchone()
            if state is None or not state[0] or not state[1]:
                raise ValueError("sync-arxiv requires a completed, published baseline")

        alias = args.alias or settings.SCHOLAR_INDEX_ALIAS
        redis = Redis.from_url(
            settings.REDIS_URL,
            socket_timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
        )
        try:
            async with (
                create_elasticsearch(
                    settings.ELASTICSEARCH_URIS,
                    settings.ELASTICSEARCH_USERNAME,
                    settings.ELASTICSEARCH_PASSWORD,
                    settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
                ) as es,
                httpx.AsyncClient(
                    timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS
                ) as s2_http,
            ):
                transport = S2Transport(s2_http, redis, settings.S2_API_KEY)
                s2_provider = S2Provider(transport)
                datasets = S2DatasetsClient(transport)
                cache = RedisPaperEnrichmentCache(redis, namespace=f"{alias}:s2")

                async def publish_pending():
                    return await publish_arxiv(db, es, alias)

                if args.command == "sync-arxiv":
                    async with httpx.AsyncClient(
                        timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS
                    ) as oai_http:
                        return await sync_arxiv(
                            db,
                            ArxivOaiHarvester(oai_http),
                            s2_provider,
                            from_date=args.from_date,
                            batch_size=args.batch_size,
                            publish=publish_pending,
                            cache=cache,
                        )

                async with httpx.AsyncClient(
                    timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS
                ) as download_http:
                    return await sync_s2_diffs(
                        db,
                        datasets,
                        download_http,
                        args.workdir or SERVICE_ROOT / ".runtime" / "s2-diff-work",
                        publish_pending=publish_pending,
                        invalidate_aliases=cache.invalidate,
                        base_release=args.base_release,
                        target_release=args.target_release,
                        tldr_model=args.tldr_model,
                        batch_size=args.batch_size,
                    )
        finally:
            await redis.aclose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="arXiv snapshot ingestion and versioned publication"
    )
    parser.add_argument(
        "command",
        choices=[
            "import-arxiv",
            "import-s2",
            "publish",
            "status",
            "sync-arxiv",
            "sync-s2",
        ],
    )
    parser.add_argument(
        "--db", default=str(SERVICE_ROOT / ".runtime" / "scholar.duckdb")
    )
    parser.add_argument(
        "--source",
        default=str(
            SERVICE_ROOT / "third_party_data" / "arxiv-metadata-oai-snapshot.json"
        ),
    )
    parser.add_argument("--alias")
    parser.add_argument("--batch-size", type=int)
    s2_root = SERVICE_ROOT.parent.parent / "s2_download"
    parser.add_argument("--papers-dir", default=str(s2_root / "papers"))
    parser.add_argument("--tldrs-dir", default=str(s2_root / "tldrs"))
    parser.add_argument("--release-id", help="本地 S2 输入声明的 release，必须显式提供")
    parser.add_argument("--tldr-model")
    parser.add_argument("--max-shards", type=int)
    parser.add_argument(
        "--max-batches", type=int, help="本次批次预算；未完成不会切换别名"
    )
    parser.add_argument("--from", "--from-date", dest="from_date")
    parser.add_argument("--base", "--base-release", dest="base_release")
    parser.add_argument("--target", "--target-release", dest="target_release")
    parser.add_argument("--workdir")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.batch_size is None:
        args.batch_size = (
            100
            if args.command == "sync-arxiv"
            else 50000
            if args.command in ("import-s2", "sync-s2")
            else 5000
        )
    if args.batch_size < 1 or (args.max_batches is not None and args.max_batches < 1):
        parser.error("batch sizes and budgets must be positive")
    if args.max_shards is not None and args.max_shards < 1:
        parser.error("max-shards must be positive")
    if args.command == "import-s2" and not args.release_id:
        parser.error("import-s2 requires --release-id")
    if args.command == "import-s2" and args.tldr_model is None:
        args.tldr_model = "tldr@v2.0.0"
    if args.max_batches is not None and args.command not in ("import-arxiv", "publish"):
        parser.error("--max-batches is only valid for import-arxiv and publish")
    if args.from_date is not None and args.command != "sync-arxiv":
        parser.error("--from is only valid for sync-arxiv")
    if (
        args.base_release is not None or args.target_release is not None
    ) and args.command != "sync-s2":
        parser.error("--base and --target are only valid for sync-s2")
    if args.workdir is not None and args.command != "sync-s2":
        parser.error("--workdir is only valid for sync-s2")
    if args.tldr_model is not None and args.command not in ("import-s2", "sync-s2"):
        parser.error("--tldr-model is only valid for import-s2 and sync-s2")
    print(orjson.dumps(asyncio.run(run(args))).decode())


if __name__ == "__main__":
    main()
