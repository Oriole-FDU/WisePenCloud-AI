"""独立 ETL 命令：HTTP 生命周期不创建数据库写者或后台导入任务。"""

import argparse
import asyncio

import orjson

from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from scholar.core.storage.elasticsearch.paper_repository import create_elasticsearch
from scholar.etl.arxiv import database_status, import_arxiv, open_database
from scholar.etl.publish import publish_arxiv


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
    parser.add_argument("command", choices=["import-arxiv", "publish", "status"])
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
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument(
        "--max-batches", type=int, help="本次批次预算；未完成不会切换别名"
    )
    args = parser.parse_args()
    if args.batch_size < 1 or (args.max_batches is not None and args.max_batches < 1):
        parser.error("batch sizes and budgets must be positive")
    print(orjson.dumps(asyncio.run(run(args))).decode())


if __name__ == "__main__":
    main()
