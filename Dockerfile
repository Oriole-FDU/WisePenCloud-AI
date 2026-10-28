# =============================================================================
# wisepen-cloud-ai/Dockerfile
# -----------------------------------------------------------------------------
# 通用 Dockerfile
# =============================================================================

# ---- 构建阶段：利用 uv 官方镜像极速安装依赖 ----
FROM ghcr.io/astral-sh/uv:0.6-python3.11-bookworm-slim AS builder

WORKDIR /app

ARG SERVICE_PROJECT
ARG PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV UV_DEFAULT_INDEX=${PYPI_INDEX_URL}
ENV UV_INDEX_URL=${PYPI_INDEX_URL}
ENV UV_CACHE_DIR=/root/.cache/uv
ENV UV_LINK_MODE=copy

# 先复制依赖定义文件，利用 Docker layer 缓存——源码变更时此层不会重建
COPY pyproject.toml uv.lock ./
# workspace 成员的 pyproject 预拷（仅为 layer cache）；新增 service 时在下方追加一行
COPY services/wisepen-common/pyproject.toml         services/wisepen-common/pyproject.toml
COPY services/wisepen-chat-service/pyproject.toml   services/wisepen-chat-service/pyproject.toml
COPY services/wisepen-mcp-service/pyproject.toml    services/wisepen-mcp-service/pyproject.toml
COPY services/wisepen-sandbox-service/pyproject.toml services/wisepen-sandbox-service/pyproject.toml

# 只预装当前服务及其依赖的第三方包，不把整个 workspace 的依赖带入镜像。
RUN uv sync --frozen --no-dev --no-install-workspace --package ${SERVICE_PROJECT} \
    --default-index ${PYPI_INDEX_URL}

# 复制全部源码并安装 workspace 包
COPY services/ services/
RUN uv sync --frozen --no-dev --package ${SERVICE_PROJECT} \
    --default-index ${PYPI_INDEX_URL}


# ---- 运行阶段：仅包含运行时，不含 uv / 编译工具链 ----
FROM python:3.11-slim-bookworm

ARG SERVICE_PROJECT

# 加速构建过程
ARG DEBIAN_MIRROR_BASE=https://mirrors.aliyun.com
ARG DOCKER_CE_MIRROR=https://mirrors.aliyun.com/docker-ce/linux/debian
ARG DOCKER_CLI_VERSION=5:27.5.1-1~debian.12~bookworm
RUN if [ "$SERVICE_PROJECT" = "wisepen-sandbox-service" ]; then \
        for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
            if [ -f "$source_file" ]; then \
                sed -Ei "s|https?://deb\\.debian\\.org|${DEBIAN_MIRROR_BASE}|g" "$source_file"; \
            fi; \
        done && \
        install -m 0755 -d /etc/apt/keyrings && \
        python -c 'import pathlib, sys, urllib.request; pathlib.Path(sys.argv[2]).write_bytes(urllib.request.urlopen(sys.argv[1], timeout=30).read())' \
            "${DOCKER_CE_MIRROR}/gpg" /etc/apt/keyrings/docker.asc && \
        . /etc/os-release && \
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] ${DOCKER_CE_MIRROR} ${VERSION_CODENAME} stable" \
            > /etc/apt/sources.list.d/docker-ce.list && \
        apt-get update && \
        apt-get install -y --no-install-recommends docker-ce-cli="${DOCKER_CLI_VERSION}" && \
        docker --version && \
        rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services /app/services

ENV PATH="/app/.venv/bin:$PATH"

# caller 必传：缺失会导致 WORKDIR 解析为 /app/services//src，容器启动即崩
ARG SERVICE_DIR
ARG SERVICE_PKG
ARG SERVICE_PORT
ENV SERVICE_DIR=${SERVICE_DIR}
ENV SERVICE_PKG=${SERVICE_PKG}
ENV SERVICE_PORT=${SERVICE_PORT}

WORKDIR /app/services/${SERVICE_DIR}/src

EXPOSE ${SERVICE_PORT}

# 用 sh -c + exec 将 uvicorn 升为 PID 1，确保 docker stop 时 SIGTERM 被正确捕获
CMD ["sh", "-c", "exec uvicorn ${SERVICE_PKG}.main:app --host 0.0.0.0 --port ${SERVICE_PORT}"]
