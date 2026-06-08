from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from chat.core.config.app_settings import settings


@dataclass(frozen=True, slots=True)
class QdrantCollectionConfig:
    """Qdrant collection 配置。

    - collection_name: RAG chunk 向量集合名。
    - dense_vector_size: dense embedding 维度。
    - dense_distance: dense vector 距离度量。
    - bm25 sparse vector 使用 Qdrant 内置 BM25 / IDF 能力。
    """

    collection_name: str
    dense_vector_size: int
    dense_distance: models.Distance = models.Distance.COSINE


class QdrantCollectionManager:
    """Qdrant collection 管理器。

    - 负责确保 RAG collection 存在。
    - 创建 dense named vector。
    - 创建 bm25 sparse named vector。
    - 不负责 upsert point。
    - 不负责检索。
    """

    def __init__(
        self,
        client: AsyncQdrantClient,
        config: QdrantCollectionConfig,
    ) -> None:
        """初始化对象依赖。"""
        self._client = client
        self._config = config

    async def ensure_collection(self) -> None:
        """确保 collection 存在。"""

        if await self._client.collection_exists(
            collection_name=self._config.collection_name
        ):
            return

        await self._client.create_collection(
            collection_name=self._config.collection_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=self._config.dense_vector_size,
                    distance=self._config.dense_distance,
                ),
            },
            sparse_vectors_config={
                "bm25": models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                ),
            },
        )


def build_qdrant_client() -> AsyncQdrantClient:
    """构造 Qdrant 官方异步客户端。"""

    return AsyncQdrantClient(
        url=f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}",
        api_key=settings.QDRANT_PASSWORD,
        prefer_grpc=False,
    )