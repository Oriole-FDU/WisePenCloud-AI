from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Dict

import torch
from transformers import MarianMTModel, MarianTokenizer, PreTrainedModel
from transformers import PreTrainedTokenizerBase

_OPUS_MODEL_MAP = {
    ("zh", "en"): "Helsinki-NLP/opus-mt-zh-en",
    ("en", "zh"): "Helsinki-NLP/opus-mt-en-zh",
}


@dataclass(frozen=True, slots=True)
class OpusMtModelBundle:
    """OPUS/Marian 单个语言方向的已加载模型资源。"""

    tokenizer: PreTrainedTokenizerBase
    model: PreTrainedModel


class OpusMtModelProvider:
    """OPUS/Marian 模型资源提供器，由容器作为全局单例注入。"""

    def __init__(self, config) -> None:
        """初始化模型提供器并解析运行设备。"""
        self.config = config
        self.device = _resolve_device(config.device)
        self._cache: Dict[str, OpusMtModelBundle] = {}
        self._load_lock = Lock()

    def get_bundle(
        self,
        *,
        source_language: str,
        target_language: str,
    ) -> OpusMtModelBundle:
        """获取指定语言方向的已加载 tokenizer 和 model。"""
        model_name = _resolve_model_name(
            source_language=source_language,
            target_language=target_language,
        )
        if model_name in self._cache:
            return self._cache[model_name]

        with self._load_lock:
            if model_name in self._cache:
                return self._cache[model_name]

            tokenizer = MarianTokenizer.from_pretrained(model_name)
            model = MarianMTModel.from_pretrained(model_name)
            model.to(self.device)
            model.eval()

            bundle = OpusMtModelBundle(tokenizer=tokenizer, model=model)
            self._cache[model_name] = bundle
            return bundle


def _resolve_model_name(
    *,
    source_language: str,
    target_language: str,
) -> str:
    """解析语言方向对应的 OPUS/Marian 模型名称。"""
    return _OPUS_MODEL_MAP[(source_language.lower(), target_language.lower())]


def _resolve_device(device: str) -> str:
    """在 provider 初始化时解析一次设备配置。"""
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("translation device cuda is unavailable")
    if device not in {"cpu", "cuda"}:
        raise RuntimeError(f"unsupported translation device: {device}")
    return device
