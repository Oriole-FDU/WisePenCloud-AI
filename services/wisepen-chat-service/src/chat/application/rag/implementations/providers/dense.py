import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List

import litellm

from chat.application.rag.domain.index_chunks import DenseVector
from chat.application.rag.domain.ports import (
    RagDenseEmbeddingCacheLookup,
    RagDenseEmbeddingCacheRepository,
    RagDenseEmbeddingCacheWrite,
)


class DenseEmbeddingError(RuntimeError):
    """Dense embedding 生成失败。"""


class DenseEmbeddingClient(ABC):
    """Dense embedding 客户端接口。

    - 负责将文本转换为 dense vector。
    - 不负责 embedding cache。
    - 不负责 Qdrant 写入。
    - 不负责 indexing text 构造。
    """

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[DenseVector]:
        """处理当前流程。"""
        pass


@dataclass(frozen=True, slots=True)
class LiteLLMDenseEmbeddingClientConfig:
    """LiteLLM dense embedding 配置。

    - model 使用 LiteLLM 模型名。
    - api_base 复用默认 LLM 网关地址。
    - api_key 复用默认 LLM 网关密钥。
    """

    model: str
    api_base: str
    api_key: str
    dimensions: int


class LiteLLMDenseEmbeddingClient(DenseEmbeddingClient):
    """LiteLLM dense embedding 客户端。

    - 负责将 semantic_indexing_text 转换为 dense vector。
    - 不负责 embedding cache。
    - 不负责 Qdrant 写入。
    - 不负责 indexing text 构造。
    """

    def __init__(self, config: LiteLLMDenseEmbeddingClientConfig) -> None:
        """初始化对象依赖。"""
        self._config = config

    async def embed_texts(self, texts: List[str]) -> List[DenseVector]:
        """批量生成 dense embedding。

        Args:
        - texts: 待向量化文本列表.

        Returns:
        - dense vector 列表，顺序与输入 texts 一致。
        """
        if not texts:
            return []

        try:
            response = await litellm.aembedding(
                model=self._config.model,
                input=texts,
                api_base=self._config.api_base,
                api_key=self._config.api_key,
                dimensions=self._config.dimensions,
            )
        except Exception as e:
            raise DenseEmbeddingError(f"LiteLLM embedding request failed: {e}") from e

        vectors: List[DenseVector] = [
            item["embedding"]
            for item in response.data
        ]

        if len(vectors) != len(texts):
            raise DenseEmbeddingError(
                "Dense embedding result count does not match input count."
            )

        return vectors


class CachedDenseEmbeddingClient(DenseEmbeddingClient):
    """带缓存的 dense embedding client。

    - 用于索引侧 semantic_indexing_text embedding。
    - 先查 dense embedding cache。
    - cache miss 时调用 inner_client。
    - 不用于 query embedding cache。
    """

    def __init__(
            self,
            inner_client: DenseEmbeddingClient,
            cache_repository: RagDenseEmbeddingCacheRepository,
            model_version: str,
    ) -> None:
        """初始化对象依赖。"""
        self._inner_client = inner_client
        self._cache_repository = cache_repository
        self._model_version = model_version

    async def embed_texts(self, texts: List[str]) -> List[DenseVector]:
        """处理当前流程。"""
        if not texts:
            return []

        text_hashes = [
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            for text in texts
        ]

        # 用 text_hash 作为 lookup 的唯一映射
        cached_vectors = await self._cache_repository.get_vectors(
            [
                RagDenseEmbeddingCacheLookup(
                    lookup_id=text_hash,
                    dense_embedding_model_version=self._model_version,
                    text_hash=text_hash,
                )
                for text_hash in set(text_hashes)
            ]
        )

        missing_map = {
            h: t for h, t in zip(text_hashes, texts)
            if h not in cached_vectors.keys()
        }
        generated_vectors_by_hash: Dict[str, DenseVector] = {}
        if missing_map:
            missing_hashes = list(missing_map.keys())
            missing_texts = list(missing_map.values())

            generated_vectors = await self._inner_client.embed_texts(missing_texts)
            if len(generated_vectors) != len(missing_hashes):
                raise DenseEmbeddingError("Dense embedding miss result count mismatch.")

            # 写入缓存
            cache_writes = [
                RagDenseEmbeddingCacheWrite(
                    dense_embedding_model_version=self._model_version,
                    text_hash=text_hash,
                    vector=vector,
                )
                for text_hash, vector in zip(missing_hashes, generated_vectors)
            ]

            await self._cache_repository.put_vectors(cache_writes)

            generated_vectors_by_hash.update({
                h: v for h, v in zip(missing_hashes, generated_vectors)
            })

        return list((cached_vectors | generated_vectors_by_hash).values())


