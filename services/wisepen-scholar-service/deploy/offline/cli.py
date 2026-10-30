"""独立 ETL 命令：HTTP 生命周期不创建数据库写者或后台导入任务。"""

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

import httpx
import orjson
from offline.arxiv import database_status, import_arxiv, open_database
from offline.arxiv_oai import ArxivOaiHarvester
from offline.arxiv_sync import recover_s2_missing, sync_arxiv
from offline.publish import publish_arxiv
from offline.s2 import apply_s2_projection, import_s2
from offline.s2_api import S2SyncClient
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from scholar.core.storage.elasticsearch.paper_repository import create_elasticsearch
from scholar.core.storage.redis.paper_cache import RedisPaperLookupCache
from scholar.core.providers import S2Transport


def resolve_proxy(proxy_port: int | None = None) -> str | None:
    if proxy_port is not None:
        return f"http://127.0.0.1:{proxy_port}"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    return None


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

        if args.command == "encode-embeddings":
            from scholar.ml.specter import DEFAULT_MODELS

            from offline.embeddings import document_worker, generate_embeddings

            async with document_worker(
                Path(args.gpu_python or settings.SCHOLAR_EMBEDDING_GPU_PYTHON),
                Path(
                    args.models
                    or settings.SCHOLAR_EMBEDDING_MODELS_PATH
                    or DEFAULT_MODELS
                ),
            ) as worker:
                return await generate_embeddings(
                    db, worker, batch_size=args.batch_size, max_batches=args.max_batches
                )

        if args.command == "publish":
            qdrant = AsyncQdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
                trust_env=False,
                timeout=60.0,
            )
            try:
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
                        qdrant=qdrant,
                        batch_size=args.batch_size,
                        max_batches=args.max_batches,
                    )
            finally:
                await qdrant.close()

        if args.command in ("sync-arxiv", "recover-s2"):
            state = db.execute("SELECT complete,published FROM import_state").fetchone()
            if state is None or not state[0] or not state[1]:
                raise ValueError("sync-arxiv requires a completed, published baseline")

        alias = args.alias or settings.SCHOLAR_INDEX_ALIAS
        redis = Redis.from_url(
            settings.REDIS_URL,
            socket_timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
        )
        try:
            qdrant = AsyncQdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
                trust_env=False,
                timeout=60.0,
            )
            proxy_url = resolve_proxy(getattr(args, "proxy_port", None))
            if proxy_url:
                print(f"[Proxy] 使用代理: {proxy_url}", flush=True)
            else:
                print("[Proxy] 未使用代理 (直连)", flush=True)
            s2_key = os.environ.get("S2_API_KEY") or settings.S2_API_KEY
            try:
                async with (
                    create_elasticsearch(
                        settings.ELASTICSEARCH_URIS,
                        settings.ELASTICSEARCH_USERNAME,
                        settings.ELASTICSEARCH_PASSWORD,
                        settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
                    ) as es,
                    httpx.AsyncClient(
                        proxy=proxy_url,
                        timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
                    ) as s2_http,
                    S2Transport(
                        s2_http, redis, s2_key, offline=True,
                        nokey_interval=settings.SCHOLAR_S2_NOKEY_INTERVAL_SECONDS,
                        key_interval=settings.SCHOLAR_S2_KEY_INTERVAL_SECONDS,
                        batch_max_ids=settings.SCHOLAR_S2_BATCH_MAX_IDS,
                        request_timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
                    ) as transport,
                ):
                    if s2_key:
                        print(f"[S2] 已成功加载 S2 API Key (前缀: {s2_key[:6]}***)", flush=True)
                    s2_provider = S2SyncClient(transport)
                    s2_cache = RedisPaperLookupCache(redis, namespace=f"{alias}:s2")
                    arxiv_cache = RedisPaperLookupCache(redis, namespace=f"{alias}:arxiv")

                    async def publish_pending():
                        return await publish_arxiv(db, es, alias, qdrant=qdrant)

                    if args.command == "recover-s2":
                        return await recover_s2_missing(
                            db,
                            s2_provider,
                            batch_size=args.batch_size,
                            max_batches=args.max_batches,
                            publish=publish_pending,
                            cache=s2_cache,
                        )

                    if args.command in ("sync-arxiv", "recover-s2"):
                        async with httpx.AsyncClient(
                            proxy=proxy_url,
                            timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
                        ) as oai_http:
                            return await sync_arxiv(
                                db,
                                ArxivOaiHarvester(oai_http),
                                s2_provider,
                                from_date=args.from_date,
                                batch_size=args.batch_size,
                                publish=publish_pending,
                                cache=s2_cache,
                                arxiv_cache=arxiv_cache,
                                cache_harvest=True,
                            )
            finally:
                await qdrant.close()

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
            "encode-embeddings",
            "recover-s2",
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
    parser.add_argument("--gpu-python")
    parser.add_argument("--models")
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
    parser.add_argument(
        "--proxy-port",
        type=int,
        help="显式指定代理端口（例如 7897），默认自动发现本地 7897 或系统代理",
    )
    parser.add_argument("--from", "--from-date", dest="from_date")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.batch_size is None:
        args.batch_size = (
            8
            if args.command == "encode-embeddings"
            else 500
            if args.command in ("sync-arxiv", "recover-s2")
            else 50000
            if args.command == "import-s2"
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
    if args.max_batches is not None and args.command not in (
            "import-arxiv",
            "publish",
            "encode-embeddings",
    ):
        parser.error(
            "--max-batches is only valid for import-arxiv, publish and encode-embeddings"
        )
    if args.from_date is not None and args.command != "sync-arxiv":
        parser.error("--from is only valid for sync-arxiv")
    if args.tldr_model is not None and args.command != "import-s2":
        parser.error("--tldr-model is only valid for import-s2")
    print(orjson.dumps(asyncio.run(run(args))).decode())


if __name__ == "__main__":
    main()
