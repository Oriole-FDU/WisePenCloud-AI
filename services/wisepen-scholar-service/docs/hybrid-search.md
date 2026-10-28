# 本地混合检索

## 查询编码进程

`adapters 1.3.0` 要求 `transformers ~=4.57.6`，与 workspace 当前的 5.x 冲突。
查询进程使用独立 CPU 环境，模型代码仍在 Scholar 内，不注册新微服务。
在仓库根目录准备一次（不会同步 workspace）：

```powershell
uv venv --python 3.12 services/wisepen-scholar-service/.runtime/embedding-cpu
uv pip install --python services/wisepen-scholar-service/.runtime/embedding-cpu/Scripts/python.exe -r services/wisepen-scholar-service/requirements/embedding-cpu.txt
uv run --no-sync python services/wisepen-scholar-service/scripts/serve_embeddings.py
```

默认监听 `127.0.0.1:8013`，提供 `/health` 和 `/v1/embeddings`。
模型启动预热完成才提供服务；固定本地 base 与 adhoc adapter 修订，不在线下载模型或切换 adapter。
`--models` 指向 HF cache 目录，`--python` 可指定已有兼容 CPU 环境。
GPU 论文 worker 使用相同编码模块的 `document-worker` 模式，只接受论文批次，不开放 HTTP。

## 验证记录

- HF 来源和应用搜索相关行为测试 52 项通过。
- embeddings HTTP 协议与公共 `EmbeddingClient` 往返、错误输入测试 5 项通过。
- 本地独立 CPU 进程真实生成 768 维查询向量，短查询首次测量约 1.26 秒；此数值不是性能承诺。
