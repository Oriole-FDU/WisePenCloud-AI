"""本地 SPECTER2 编码与 GPU 论文 worker；CPU 模型由 main 生命周期管理。"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import threading
from pathlib import Path

# --- 模型修订 -------------------------------------------------------------
# 模型修订、输入与精度共同决定向量身份，不能作为在线调参项独立变更。
# 此模块仅在实例化编码器时导入 Torch，离线发布可直接复用这些常量。
BASE_REVISION = "3447645e1def9117997203454fa4495937bfbd83"
DOCUMENT_REVISION = "2081559630a80fc5851d8f798a05ba81e9468089"
QUERY_REVISION = "3f4448817028388648a74349ece07af4518ec5bd"

# --- 几何参数 -------------------------------------------------------------
DIMENSIONS = 768
MAX_LENGTH = 512

# --- 路径与签名 -----------------------------------------------------------
# 项目根下的 models/ 目录：存放 HuggingFace 离线快照。
DEFAULT_MODELS = Path(__file__).resolve().parents[3] / "models"
MODEL_SIGNATURE = hashlib.sha256(
    json.dumps(
        {
            "base": BASE_REVISION,
            "document_adapter": DOCUMENT_REVISION,
            "query_adapter": QUERY_REVISION,
            "pooling": "cls",
            "max_length": MAX_LENGTH,
            "normalize": False,
            "document_precision": "fp16",
            "query_precision": "fp32",
        },
        sort_keys=True,
    ).encode()
).hexdigest()
QUERY_MODEL = f"specter2-adhoc-{MODEL_SIGNATURE[:16]}"


class SpecterEncoder:
    """启动时固定一个 adapter；查询服务不会按请求切换论文 adapter。"""

    def __init__(self, models: Path, *, query: bool, threads: int = 2):
        import torch
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer

        torch.set_num_threads(threads)
        self.torch = torch
        self.query = query
        # 查询走 CPU、文档走 GPU；GPU 缺失时不做 CPU 回退，直接失败。
        self.device = "cpu" if query else "cuda"
        if not query and not torch.cuda.is_available():
            raise RuntimeError(
                "GPU document encoding requires CUDA; CPU fallback is disabled"
            )

        # 基础模型与 adapter 快照：都从本地路径加载，禁止联网。
        base = (
            models
            / "models--allenai--specter2_base"
            / "snapshots"
            / BASE_REVISION
        )
        adapter_name = (
            "models--allenai--specter2_adhoc_query"
            if query
            else "models--allenai--specter2"
        )
        adapter_revision = QUERY_REVISION if query else DOCUMENT_REVISION
        adapter = models / adapter_name / "snapshots" / adapter_revision

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(base), local_files_only=True
        )
        self.model = AutoAdapterModel.from_pretrained(
            str(base), local_files_only=True
        )
        self.model.load_adapter(str(adapter), load_as="active", set_active=True)
        self.model.to(self.device)
        if not query:
            self.model.half()
        self.model.eval()
        self._lock = threading.Lock()

    def encode(self, inputs: list[str] | list[dict]) -> list[list[float]]:
        # 查询直接编码文本；文档编码拼接 title 与 abstract，用 [SEP] 分隔。
        texts = (
            inputs
            if self.query
            else [
                item["title"] + self.tokenizer.sep_token + item["abstract"]
                for item in inputs
            ]
        )
        # HTTP 线程池可能并发调度；串行化推理，避免模型状态被并发访问。
        with self._lock, self.torch.inference_mode():
            tokens = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
                return_token_type_ids=False,
            ).to(self.device)
            # 取 [CLS] 池化向量；fp16 推理后统一转 float32 再序列化。
            return (
                self.model(**tokens)
                .last_hidden_state[:, 0, :]
                .float()
                .cpu()
                .tolist()
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["document-worker"])
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()

    query = False
    # 模型库的启动输出不能污染 GPU worker 的 JSONL 协议。
    with contextlib.redirect_stdout(sys.stderr):
        encoder = SpecterEncoder(args.models, query=query, threads=args.threads)
        encoder.encode(
            ["warmup"] if query else [{"title": "warmup", "abstract": ""}]
        )
    print(json.dumps({"ready": True, "signature": MODEL_SIGNATURE}), flush=True)

    for line in sys.stdin:
        batch = json.loads(line)
        print(
            json.dumps({"vectors": encoder.encode(batch)}),
            flush=True,
        )


if __name__ == "__main__":
    main()