"""隔离环境中的常驻推理：CPU 查询 HTTP 进程，或 GPU 论文 JSONL 子进程。"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import struct
import sys
import threading
from pathlib import Path

from scholar.core.embedding_profile import (
    DEFAULT_MODELS,
    DIMENSIONS,
    MAX_LENGTH,
    MODEL_SIGNATURE,
    QUERY_MODEL,
    model_paths,
)


class SpecterEncoder:
    """启动时固定一个 adapter；查询服务不会按请求切换论文 adapter。"""

    def __init__(self, models: Path, *, query: bool, threads: int = 2):
        import torch
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer

        torch.set_num_threads(threads)
        self.torch = torch
        self.query = query
        self.device = "cpu" if query else "cuda"
        if not query and not torch.cuda.is_available():
            raise RuntimeError(
                "GPU document encoding requires CUDA; CPU fallback is disabled"
            )
        base, adapter = model_paths(models, query=query)
        self.tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True)
        self.model = AutoAdapterModel.from_pretrained(str(base), local_files_only=True)
        self.model.load_adapter(str(adapter), load_as="active", set_active=True)
        self.model.to(self.device)
        if not query:
            self.model.half()
        self.model.eval()
        self._lock = threading.Lock()

    def encode(self, inputs: list[str] | list[dict]) -> list[list[float]]:
        texts = (
            inputs
            if self.query
            else [
                item["title"] + self.tokenizer.sep_token + item["abstract"]
                for item in inputs
            ]
        )
        # HTTP 线程池可能同时调度多个请求；模型仅允许一个 CPU 推理批次执行。
        with self._lock, self.torch.inference_mode():
            tokens = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
                return_token_type_ids=False,
            ).to(self.device)
            return (
                self.model(**tokens).last_hidden_state[:, 0, :].float().cpu().tolist()
            )


def create_query_app(encoder):
    """最小 OpenAI embeddings 协议；只接受当前固定模型、字符串及 768 维。"""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Scholar query embeddings")

    @app.get("/health")
    def health():
        return {"status": "ready", "model": QUERY_MODEL, "signature": MODEL_SIGNATURE}

    @app.post("/v1/embeddings")
    def embeddings(payload: dict):
        inputs = payload.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        if (
            payload.get("model") != QUERY_MODEL
            or payload.get("dimensions", DIMENSIONS) != DIMENSIONS
            or payload.get("encoding_format", "float") not in ("float", "base64")
            or not isinstance(inputs, list)
            or not inputs
            or any(not isinstance(text, str) or not text.strip() for text in inputs)
        ):
            raise HTTPException(
                422, "Expected the fixed query model and non-empty text inputs"
            )
        vectors = encoder.encode(inputs)
        if payload.get("encoding_format") == "base64":
            # OpenAI SDK 默认请求 base64，公共 EmbeddingClient 会还原 float32。
            vectors = [
                base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode()
                for v in vectors
            ]
        return {
            "object": "list",
            "model": QUERY_MODEL,
            "data": [
                {"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(vectors)
            ],
        }

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["serve-query", "document-worker"])
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    query = args.mode == "serve-query"
    # 模型库的启动输出不能污染 GPU worker 的 JSONL 协议。
    with contextlib.redirect_stdout(sys.stderr):
        encoder = SpecterEncoder(args.models, query=query, threads=args.threads)
        encoder.encode(["warmup"] if query else [{"title": "warmup", "abstract": ""}])
    if query:
        import uvicorn

        uvicorn.run(
            create_query_app(encoder), host="127.0.0.1", port=args.port, workers=1
        )
    else:
        print(json.dumps({"ready": True, "signature": MODEL_SIGNATURE}), flush=True)
        for line in sys.stdin:
            batch = json.loads(line)
            print(json.dumps({"vectors": encoder.encode(batch)}), flush=True)


if __name__ == "__main__":
    main()
