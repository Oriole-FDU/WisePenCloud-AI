"""维护窗口内修复显式资源的上下文与全部向量，不推进 publication。"""

import argparse
import asyncio
import json
import os
from contextlib import AsyncExitStack

from beanie import init_beanie
from dataclasses import asdict, dataclass
from pathlib import Path

from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from qdrant_client import AsyncQdrantClient

from common.logger import error
from common.utils.ai_clients import EmbeddingClient, InstructionClient
from rag.domain.storage.publication import PublicationStore
from rag.domain.entities import ChunkEntity, DocumentEntity, ResourcePublicationEntity
from rag.core.persistence.mongo.content import MongoContentStorage
from rag.core.persistence.mongo.publication import MongoPublicationStore
from rag.core.persistence.qdrant.vector import QdrantVectorStorage
from rag.domain.models import ContentRevision
from rag.ingestion.indexers import ChunkFactsIndexer, VectorIndexer
from rag.ingestion.transformers.contextualizer import ContextStatus, Contextualizer
from rag.domain.storage.content import ContentReader
from rag.domain.storage.vector import IndexableField, IndexKind


@dataclass(slots=True)
class RepairReport:
    """块级增强统计与资源级跳过/错误分开，部分写入保留供重跑。"""

    generated: int = 0
    reused: int = 0
    fallback: int = 0
    failed: int = 0
    skipped: int = 0
    repaired: int = 0
    document_errors: int = 0

    @property
    def exit_code(self) -> int:
        return int(bool(self.failed or self.document_errors))


async def repair_resources(
        resource_ids: list[str],
        *,
        publication: PublicationStore,
        content: ContentReader,
        contextualizer: Contextualizer,
        facts: ChunkFactsIndexer,
        vectors: VectorIndexer,
) -> RepairReport:
    """复用当前 applied 事实；模型降级可落库，必要写入失败不能计为修复完成。"""
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
                resource_id=resource_id,
                content_revision=revision.content_revision,
            )
            # 空白正文的已发布文档允许零 Chunk；不推断另一套分块结果。
            previous = {
                chunk.chunk_id: (
                    chunk.metadata.get("context_status"),
                    chunk.metadata.get("context_version"),
                    chunk.metadata.get("contextual_context"),
                )
                for chunk in chunks
            }
            await contextualizer.transform(chunks, document=document)
            for chunk in chunks:
                status = chunk.metadata.get("context_status")
                if status == ContextStatus.OK.value:
                    current = (
                        status,
                        chunk.metadata.get("context_version"),
                        chunk.metadata.get("contextual_context"),
                    )
                    if previous[chunk.chunk_id] == current:
                        report.reused += 1
                    else:
                        report.generated += 1
                elif status == ContextStatus.FALLBACK.value:
                    report.fallback += 1
                elif status == ContextStatus.FAILED.value:
                    report.failed += 1
            # 生成可能耗时较长；写入前复核完整发布状态，变化则不写任何 Chunk。
            current_states = await publication.get_states([resource_id])
            current = next(
                (item for item in current_states if item.resource_id == resource_id),
                None,
            )
            if current != state:
                report.skipped += 1
                continue
            await facts.index(chunks)
            # 成功缓存只证明 LLM 结果可复用，不能证明上次向量写入完成。
            await vectors.index(chunks)
            report.repaired += 1
        except Exception as exc:  # noqa: BLE001
            # 每份文档是离线失败边界；不回滚、不重试，继续其他显式资源。
            error("context_repair_failed", exc=exc, resource_id=resource_id)
            report.document_errors += 1
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-id", action="append", default=[])
    parser.add_argument("--resource-file", type=Path)
    # 所有配置显式来自 CLI 或专用环境变量；不加载应用配置和 Nacos。
    for name in (
            "mongo-url",
            "mongo-database",
            "qdrant-url",
            "instruction-base-url",
            "instruction-api-key",
            "instruction-model",
            "embedding-base-url",
            "embedding-api-key",
            "embedding-model",
    ):
        parser.add_argument(
            f"--{name}",
            default=os.getenv(f"RAG_REPAIR_{name.upper().replace('-', '_')}"),
        )
    parser.add_argument(
        "--qdrant-api-key", default=os.getenv("RAG_REPAIR_QDRANT_API_KEY")
    )
    parser.add_argument(
        "--vector-collection",
        default=os.getenv("RAG_REPAIR_VECTOR_COLLECTION", "document_chunk_vectors_v4"),
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=os.getenv("RAG_REPAIR_EMBEDDING_DIMENSIONS"),
    )
    parser.add_argument("--max-context-tokens", type=int, default=8000)
    parser.add_argument("--target-context-chars", type=int, default=100)
    parser.add_argument("--max-output-tokens", type=int, default=150)
    parser.add_argument("--before-ratio", type=float, default=0.5)
    parser.add_argument("--outline-token-ratio", type=float, default=0.15)
    parser.add_argument("--max-concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        file_ids = (
            args.resource_file.read_text(encoding="utf-8-sig").splitlines()
            if args.resource_file
            else []
        )
    except OSError as exc:
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
            "qdrant_url",
            "instruction_base_url",
            "instruction_api_key",
            "instruction_model",
            "embedding_base_url",
            "embedding_api_key",
            "embedding_model",
            "embedding_dimensions",
    ):
        if not getattr(args, name):
            parser.error(f"missing --{name.replace('_', '-')}")
    if (
            any(
                value <= 0
                for value in (
                        args.embedding_dimensions,
                        args.max_context_tokens,
                        args.target_context_chars,
                        args.max_output_tokens,
                        args.max_concurrency,
                )
            )
            or not 0 <= args.before_ratio <= 1
            or not 0 <= args.outline_token_ratio <= 1
    ):
        parser.error("invalid context limits, ratios or embedding dimensions")
    return args


async def run(args: argparse.Namespace) -> RepairReport:
    """装配现有 adapter；注册关闭动作后再初始化，失败也释放全部客户端。"""
    async with AsyncExitStack() as stack:
        mongo = AsyncMongoClient(args.mongo_url)
        stack.push_async_callback(mongo.close)
        qdrant = AsyncQdrantClient(
            url=args.qdrant_url,
            api_key=args.qdrant_api_key,
            cloud_inference=True,
        )
        stack.push_async_callback(qdrant.close)
        instruction_api = AsyncOpenAI(
            base_url=args.instruction_base_url,
            api_key=args.instruction_api_key,
        )
        stack.push_async_callback(instruction_api.close)
        embedding_api = AsyncOpenAI(
            base_url=args.embedding_base_url,
            api_key=args.embedding_api_key,
        )
        stack.push_async_callback(embedding_api.close)
        database = mongo[args.mongo_database]
        # 维护入口只绑定现有集合；索引由服务启动时统一管理。
        await init_beanie(
            database=database,
            document_models=[DocumentEntity, ChunkEntity, ResourcePublicationEntity],
            skip_indexes=True,
        )
        content = MongoContentStorage()
        publication = MongoPublicationStore()
        vectors = QdrantVectorStorage(
            client=qdrant,
            collection=args.vector_collection,
            dense_vector_size=args.embedding_dimensions,
        )
        instruction = InstructionClient(instruction_api, model=args.instruction_model)
        embedding = EmbeddingClient(
            embedding_api,
            model=args.embedding_model,
            dimensions=args.embedding_dimensions,
        )
        contextualizer = Contextualizer(
            client=instruction,
            max_context_tokens=args.max_context_tokens,
            target_context_chars=args.target_context_chars,
            max_output_tokens=args.max_output_tokens,
            before_ratio=args.before_ratio,
            outline_token_ratio=args.outline_token_ratio,
            max_concurrency=args.max_concurrency,
        )
        await vectors.ensure_collection(
            [IndexableField("resource_id", IndexKind.KEYWORD)]
        )
        return await repair_resources(
            args.resource_ids,
            publication=publication,
            content=content,
            contextualizer=contextualizer,
            facts=ChunkFactsIndexer(content),
            vectors=VectorIndexer(embedding_client=embedding, writer=vectors),
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001
        error("context_repair_setup_failed", exc=exc)
        return 1
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
