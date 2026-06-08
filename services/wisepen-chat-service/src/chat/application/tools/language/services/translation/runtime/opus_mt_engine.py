from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any

import torch

from .model_provider import OpusMtModelProvider


@dataclass(frozen=True, slots=True)
class OpusMtEngineConfig:
    """OPUS/Marian 翻译引擎运行配置。"""

    device: str = "cpu"
    max_input_tokens: int = 512
    max_new_tokens: int = 512
    num_beams: int = 4


class OpusMtTranslationEngine:
    """OPUS/Marian 翻译执行器，只消费已注入的模型资源。"""

    def __init__(self, model_provider: OpusMtModelProvider) -> None:
        """初始化翻译执行器。"""
        self._model_provider = model_provider
        self._inference_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="translation-opusmt",
        )

    def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        """执行单段文本翻译。"""
        bundle = self._model_provider.get_bundle(
            source_language=source_language,
            target_language=target_language,
        )

        inputs = bundle.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=self._model_provider.config.max_input_tokens,
        ).to(self._model_provider.device)

        with torch.inference_mode():
            output = bundle.model.generate(
                **inputs,
                num_beams=self._model_provider.config.num_beams,
                max_new_tokens=self._model_provider.config.max_new_tokens,
            )

        return str(bundle.tokenizer.decode(output[0], skip_special_tokens=True))

    def get_source_tokenizer(
        self,
        *,
        source_language: str,
        target_language: str,
    ) -> Any:
        """返回指定语言方向的源 tokenizer。"""
        return self._model_provider.get_bundle(
            source_language=source_language,
            target_language=target_language,
        ).tokenizer

    async def translate_async(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        """在线程池中执行翻译。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._inference_executor,
            partial(
                self.translate,
                text=text,
                source_language=source_language,
                target_language=target_language,
            ),
        )

    def close(self) -> None:
        """关闭翻译执行器线程池。"""
        self._inference_executor.shutdown(wait=False, cancel_futures=True)


