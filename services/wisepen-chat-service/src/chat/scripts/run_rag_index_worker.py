"""Run the RAG index worker as an independent process.

Usage:
    cd services/wisepen-chat-service
    python -m chat.scripts.run_rag_index_worker
"""

import asyncio

from beanie import init_beanie

from chat.application.rag.runtime.persistence.entities import (
    DocumentResourceDocument,
    NoteResourceDocument,
)
from chat.application.rag.runtime.persistence.entities import (
    RagIndexManifestDocument,
)
from chat.application.rag.runtime.persistence.entities.chunk_documents import (
    RetrieveChunkDocument,
    SearchChunkDocument,
)
from chat.container import container
from chat.core.config.app_settings import settings
from chat.domain.entities import (
    ChatMessage,
    ChatSession,
    Model,
    ModelProviderMapping,
    Provider,
    Skill,
    UserSearchProviderConfig,
)
from common.logger import log_error, log_event, setup_logging_intercept

DOCUMENT_MODELS = [
    ChatSession,
    ChatMessage,
    Provider,
    Model,
    ModelProviderMapping,
    Skill,
    UserSearchProviderConfig,
    NoteResourceDocument,
    DocumentResourceDocument,
    RagIndexManifestDocument,
    RetrieveChunkDocument,
    SearchChunkDocument,
]


async def guarded(label: str, coro) -> None:
    try:
        await coro
    except Exception as e:
        log_error(label, e)


async def main() -> None:
    setup_logging_intercept(settings.LOG_LEVEL)
    log_event("RAG index worker 启动")

    await container.init_resources()
    await init_beanie(
        database=container.mongo_client()[settings.MONGODB_DB_NAME],
        document_models=DOCUMENT_MODELS,
    )

    try:
        await container.rag_index_worker_runner().start()
    finally:
        await guarded("RAG Redis Queue", container.rag_indexing_queue().close())
        await guarded("RAG Qdrant Client", container.rag_qdrant_client().close())
        await guarded("RAG Elasticsearch Client", container.rag_elasticsearch_client().close())
        await guarded("RAG ZeroEntropy Client", container.rag_zero_entropy_client().close())
        await guarded("容器资源释放失败", container.shutdown_resources())
        log_event("RAG index worker 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
