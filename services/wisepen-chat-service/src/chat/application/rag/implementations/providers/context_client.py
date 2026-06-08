from abc import ABC, abstractmethod
from dataclasses import dataclass

import litellm

from chat.application.rag.domain.index_chunks import RetrieveChunk, SearchChunk
from chat.application.rag.domain.resource_lifecycle import RagResource


class ContextBuildError(RuntimeError):
    """Context Indexing 构建失败。"""

_CONTEXT_SYSTEM_PROMPT = """\
You are a retrieval-context writer for a RAG system.

Your task: given a document excerpt (parent chunk) and a smaller passage within it \
(search chunk), write a short retrieval context that will be prepended to the search \
chunk before it is embedded and indexed. The context must make the chunk self-contained \
enough for a retrieval model to match it against relevant queries.

The context should cover three things, in natural prose:
1. What document or resource this chunk belongs to, and its general topic area.
2. The chunk's role or position in the parent content \
(e.g. "This passage defines …", "This section lists the steps for …", \
"This is the second condition under …").
3. Any ambiguous references inside the search chunk that a retriever would not be able \
to resolve on its own — such as pronouns, "this", "the above", "as described earlier", \
or bare variable/function names that only make sense in context.

Hard constraints:
- 2–4 sentences, strictly under 80 words.
- Plain prose only. No bullet points, no headers, no markdown, no code blocks.
- Ground every claim in the provided text. Do not add outside evidence_access.
- Do not answer questions, give advice, or summarise the whole document.
- Output only the context text — no preamble, no label, no trailing commentary.\
"""


# user prompt 用 XML tag 明确划定三块内容的边界，
# 避免模型把 heading 误读为 prompt 指令的一部分。
def _build_user_prompt(
    resource: RagResource,
    parent_chunk: RetrieveChunk,
    search_chunk: SearchChunk,
) -> str:
    """构建当前流程。"""
    return (
        "<resource>\n"
        f"kind: {resource.resource_kind.value}\n"
        f"id: {resource.resource_id}\n"
        f"display_name: {resource.display_name}\n"
        "</resource>\n"
        "\n"
        "<parent_chunk>\n"
        f"{parent_chunk.text}\n"
        "</parent_chunk>\n"
        "\n"
        "<search_chunk>\n"
        f"{search_chunk.text}\n"
        "</search_chunk>\n"
        "\n"
        "Write the retrieval context for the search chunk above.\n"
        "Resolve any references that are only meaningful inside the parent chunk.\n"
        "Do not repeat the search chunk text itself."
    )

@dataclass(frozen=True, slots=True)
class LiteLLMContextClientConfig:
    """LiteLLM Context Indexing 配置。"""
    model: str
    api_base: str
    api_key: str
    max_tokens: int
    temperature: float


class RagContextClient(ABC):
    """RAG Context Indexing 客户端接口。"""

    @abstractmethod
    async def generate_context(
        self,
        *,
        resource: RagResource,
        parent_chunk: RetrieveChunk,
        search_chunk: SearchChunk,
    ) -> str:
        """处理当前流程。"""
        pass


class LiteLLMContextClient(RagContextClient):
    """LiteLLM RAG Context Indexing 客户端。"""

    def __init__(self, config: LiteLLMContextClientConfig) -> None:
        """初始化对象依赖。"""
        self._config = config

    async def generate_context(
            self,
            *,
            resource: RagResource,
            parent_chunk: RetrieveChunk,
            search_chunk: SearchChunk,
    ) -> str:
        """调用 LiteLLM 生成 SearchChunk 的检索上下文。"""

        response = await litellm.acompletion(
            model=self._config.model,
            api_base=self._config.api_base,
            api_key=self._config.api_key,
            messages=[
                {"role": "system", "content": _CONTEXT_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(resource, parent_chunk, search_chunk)},
            ],
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )

        content = response.choices[0].message.content
        if content is None:
            raise ContextBuildError(
                f"LiteLLM returned empty context for search chunk: {search_chunk.chunk_id}"
            )

        context_text = content.strip()
        if not context_text:
            raise ContextBuildError(
                f"LiteLLM returned blank context for search chunk: {search_chunk.chunk_id}"
            )

        return context_text
