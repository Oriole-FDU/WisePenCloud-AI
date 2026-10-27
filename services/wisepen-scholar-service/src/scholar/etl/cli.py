"""独立 ETL 命令：HTTP 生命周期不创建数据库写者或后台导入任务。"""

import argparse
import asyncio

import orjson

from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from scholar.core.storage.elasticsearch.paper_repository import create_elasticsearch
from scholar.etl.arxiv import database_status, import_arxiv, open_database
from scholar.etl.publish import publish_arxiv
from scholar.etl.s2 import apply_s2_projection, import_s2


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


def main():
    parser = argparse.ArgumentParser(
        description="arXiv snapshot ingestion and versioned publication"
    )
    parser.add_argument(
        "command", choices=["import-arxiv", "import-s2", "publish", "status"]
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
    parser.add_argument("--tldr-model", default="tldr@v2.0.0")
    parser.add_argument("--max-shards", type=int)
    parser.add_argument(
        "--max-batches", type=int, help="本次批次预算；未完成不会切换别名"
    )
    args = parser.parse_args()
    if args.batch_size is None:
        args.batch_size = 50000 if args.command == "import-s2" else 5000
    if args.batch_size < 1 or (args.max_batches is not None and args.max_batches < 1):
        parser.error("batch sizes and budgets must be positive")
    if args.max_shards is not None and args.max_shards < 1:
        parser.error("max-shards must be positive")
    if args.command == "import-s2" and not args.release_id:
        parser.error("import-s2 requires --release-id")
    print(orjson.dumps(asyncio.run(run(args))).decode())


if __name__ == "__main__":
    main()
