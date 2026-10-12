"""维护窗口内修复图事实并可选重建 Neo4j，不重新分块或推进 publication。"""

import argparse
import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from pathlib import Path

from common.logger import error
from common.utils.ai_clients import InstructionClient
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient

from rag.application.publication.store import PublicationStore
from rag.backends.mongo.content import MongoContentStorage
from rag.backends.mongo.publication import MongoPublicationStore
from rag.backends.neo4j import Neo4jGraphStorage
from rag.core.schema import ContentRevision, Ontology
from rag.ingestion.indexers import ChunkFactsIndexer, GraphIndexer
from rag.ingestion.transformers import GraphExtractor
from rag.storage.content import ContentReader


@dataclass(slots=True)
class RepairReport:
    """只报告资源级结果；生成成功不等于持久化成功。"""

    repaired: int = 0
    skipped: int = 0
    document_errors: int = 0

    @property
    def exit_code(self) -> int:
        return int(bool(self.document_errors))


async def repair_resources(
    resource_ids: list[str],
    *,
    publication: PublicationStore,
    content: ContentReader,
    extractor: GraphExtractor,
    facts: ChunkFactsIndexer,
    graph: GraphIndexer | None = None,
) -> RepairReport:
    """当前 applied 事实逐资源修复；同资源不得存在其他 Chunk 写任务。

    模型异常停止当前资源，旧数据库事实不被清空；保存失败的部分写入保留。
    写前状态复核只适用于维护窗口，不提供并发更新协议。
    """
    report = RepairReport()
    for resource_id in dict.fromkeys(resource_ids):
        try:
            states = await publication.get_states([resource_id])
            state = next(
                (item for item in states if item.resource_id == resource_id), None
            )
            if state is None or state.destroyed or state.applied_revision is None:
                report.skipped += 1
                continue
            revision = state.applied_revision
            documents = await content.get_documents(
                [(resource_id, revision.content_revision)]
            )
            if len(documents) != 1:
                raise RuntimeError("selected document is missing or ambiguous")
            document = documents[0]
            actual = ContentRevision.create(
                resource_id=document.resource_id,
                document_version=document.document_version,
                text=document.text,
            )
            if (
                actual != revision
                or document.content_revision != revision.content_revision
            ):
                raise RuntimeError(
                    "selected document identity does not match publication"
                )
            chunks = await content.get_revision_chunks(
                resource_id=resource_id, content_revision=revision.content_revision
            )
            # 查询返回是外部事实边界；不把其他资源/版本的材料交给模型或替换写入。
            if any(
                chunk.resource_id != resource_id
                or chunk.content_revision != revision.content_revision
                for chunk in chunks
            ):
                raise RuntimeError("selected chunks do not match publication")
            chunks = await extractor.transform(chunks, document=document)
            current_states = await publication.get_states([resource_id])
            current = next(
                (item for item in current_states if item.resource_id == resource_id),
                None,
            )
            if current != state:
                report.skipped += 1
                continue
            # 图字段不改变 retrieval_text，不重写普通向量；成功缓存也不能跳过事实写入。
            await facts.index(chunks)
            # 抽取缓存不证明上次图投影完成；启用时每次重写全部来源贡献。
            if graph is not None:
                await graph.index(chunks)
            report.repaired += 1
        except Exception as exc:  # noqa: BLE001
            error("graph_repair_failed", exc=exc, resource_id=resource_id)
            report.document_errors += 1
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-id", action="append", default=[])
    parser.add_argument("--resource-file", type=Path)
    parser.add_argument("--ontology-file", type=Path, required=True)
    for name in (
        "mongo-url",
        "mongo-database",
        "instruction-base-url",
        "instruction-api-key",
        "instruction-model",
    ):
        parser.add_argument(
            f"--{name}",
            default=os.getenv(f"RAG_GRAPH_REPAIR_{name.upper().replace('-', '_')}"),
        )
    parser.add_argument("--max-input-tokens", type=int, default=8000)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--gleaning", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--with-graph", action="store_true")
    for name, default in (
        ("uri", None),
        ("username", None),
        ("password", None),
        ("database", "neo4j"),
        ("graph-name", "rag_v4"),
    ):
        parser.add_argument(
            f"--neo4j-{name}",
            default=os.getenv(
                f"RAG_GRAPH_REPAIR_NEO4J_{name.upper().replace('-', '_')}", default
            ),
        )
    args = parser.parse_args(argv)
    try:
        file_ids = (
            args.resource_file.read_text(encoding="utf-8-sig").splitlines()
            if args.resource_file
            else []
        )
        # 本体文件在连接和模型调用前解析，未知字段与不一致声明在边界拒绝。
        args.ontology = Ontology.from_file(args.ontology_file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.resource_ids = list(
        dict.fromkeys(
            value.strip() for value in [*args.resource_id, *file_ids] if value.strip()
        )
    )
    if not args.resource_ids:
        parser.error("provide --resource-id or a nonempty --resource-file")
    for name in (
        "mongo_url",
        "mongo_database",
        "instruction_base_url",
        "instruction_api_key",
        "instruction_model",
    ):
        if not getattr(args, name):
            parser.error(f"missing --{name.replace('_', '-')}")
    if any(
        value <= 0
        for value in (
            args.max_input_tokens,
            args.max_output_tokens,
            args.max_concurrency,
        )
    ):
        parser.error("token and concurrency limits must be positive")
    if args.with_graph:
        for name in ("uri", "username", "password", "database", "graph_name"):
            value = getattr(args, f"neo4j_{name}")
            if not value or not value.strip():
                parser.error(f"missing --neo4j-{name.replace('_', '-')}")
        # 图名构成后端标识符，连接前拒绝不合法配置。
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.neo4j_graph_name):
            parser.error("neo4j graph name must be a Cypher identifier")
    return args


async def run(args: argparse.Namespace) -> RepairReport:
    """显式装配 Mongo 与模型客户端，成功和异常路径均关闭资源。"""
    async with AsyncExitStack() as stack:
        mongo = AsyncMongoClient(args.mongo_url)
        stack.push_async_callback(mongo.close)
        instruction_api = AsyncOpenAI(
            base_url=args.instruction_base_url, api_key=args.instruction_api_key
        )
        stack.push_async_callback(instruction_api.close)
        database = mongo[args.mongo_database]
        content = MongoContentStorage(
            documents=database["document_revisions"], chunks=database["doc_chunks"]
        )
        publication = MongoPublicationStore(database["resource_publications"])
        extractor = GraphExtractor(
            ontology=args.ontology,
            client=InstructionClient(instruction_api, model=args.instruction_model),
            max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens,
            max_concurrency=args.max_concurrency,
            gleaning=args.gleaning,
            reuse_existing=not args.force,
        )
        graph = None
        if args.with_graph:
            driver = AsyncGraphDatabase.driver(
                args.neo4j_uri, auth=(args.neo4j_username, args.neo4j_password)
            )
            stack.push_async_callback(driver.close)
            topology = Neo4jGraphStorage(
                driver=driver,
                database=args.neo4j_database,
                graph_name=args.neo4j_graph_name,
            )
            await topology.ensure_schema()
            graph = GraphIndexer(ontology=args.ontology, writer=topology)
        return await repair_resources(
            args.resource_ids,
            publication=publication,
            content=content,
            extractor=extractor,
            facts=ChunkFactsIndexer(content),
            graph=graph,
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001
        error("graph_repair_setup_failed", exc=exc)
        return 1
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
