# ---- 构建阶段：利用 uv 官方镜像极速安装依赖 ----
FROM ghcr.io/astral-sh/uv:0.6-python3.11-bookworm-slim AS builder

WORKDIR /app

# 先复制依赖定义文件，利用 Docker layer 缓存——源码变更时此层不会重建
COPY pyproject.toml uv.lock ./
COPY services/wisepen-common/pyproject.toml services/wisepen-common/pyproject.toml
COPY services/wisepen-chat-service/pyproject.toml services/wisepen-chat-service/pyproject.toml

# 预装第三方依赖（不安装 workspace 包本身，纯缓存层）
RUN uv sync --frozen --no-dev --no-install-workspace

# 复制全部源码并安装 workspace 包
COPY services/ services/
RUN uv sync --frozen --no-dev


# ---- 运行阶段：仅包含运行时，不含 uv / 编译工具链 ----
FROM python:3.11-slim-bookworm

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services /app/services

ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app/services/wisepen-chat-service/src

EXPOSE 8000

CMD ["uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "9200"]
