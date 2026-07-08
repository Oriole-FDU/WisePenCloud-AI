#!/bin/bash
# ============================================================
# AIO Worker Container 自动部署脚本
#
# 从容器注册表拉取 AIO 镜像并启动工作容器，无需将AIO代码提交到仓库。
# 与 wisepen-sandbox-service 的容器队列模式配合使用。
#
# 用法:
#   bash deploy-aio-worker.sh [start|stop|restart|status]
#
# 环境变量:
#   AIO_IMAGE       — 镜像地址 (默认 ghcr.io/agent-infra/sandbox:latest)
#   AIO_IMAGE_CN    — 国内镜像 (默认 enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest)
#   AIO_USE_CN_MIRROR — 使用国内镜像 (默认 0; 设为 1 启用)
#   AIO_CONTAINER_NAME — 容器名 (默认 sandbox-workspace)
#   AIO_WORKSPACE_CACHE — workspace 缓存目录 (默认 /workspaces)
#   AIO_MIN_IDLE     — 预热容器数 (默认 2)
#   AIO_MAX_TOTAL    — 最大容器数 (默认 8)
# ============================================================

set -euo pipefail

# ---- 配置 ----
AIO_IMAGE="${AIO_IMAGE:-ghcr.io/agent-infra/sandbox:latest}"
AIO_IMAGE_CN="${AIO_IMAGE_CN:-enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest}"
AIO_USE_CN_MIRROR="${AIO_USE_CN_MIRROR:-0}"
AIO_CONTAINER_NAME="${AIO_CONTAINER_NAME:-sandbox-workspace}"
AIO_WORKSPACE_CACHE="${AIO_WORKSPACE_CACHE:-/workspaces}"
AIO_MIN_IDLE="${AIO_MIN_IDLE:-2}"
AIO_MAX_TOTAL="${AIO_MAX_TOTAL:-8}"

# 选择镜像
if [ "$AIO_USE_CN_MIRROR" = "1" ]; then
    IMAGE="$AIO_IMAGE_CN"
    echo "[deploy] using China mirror: $IMAGE"
else
    IMAGE="$AIO_IMAGE"
    echo "[deploy] using image: $IMAGE"
fi

# ---- 函数 ----

pull_image() {
    echo "[deploy] pulling image $IMAGE ..."
    docker pull "$IMAGE"
    echo "[deploy] image ready."
}

start_workers() {
    echo "[deploy] creating workspace cache dir: $AIO_WORKSPACE_CACHE"
    sudo mkdir -p "$AIO_WORKSPACE_CACHE"

    # 启动指定数量的 worker 容器
    for i in $(seq 1 "$AIO_MAX_TOTAL"); do
        local name="${AIO_CONTAINER_NAME}-${i}"
        if docker inspect "$name" >/dev/null 2>&1; then
            echo "[deploy] container $name exists, skipping."
            continue
        fi
        echo "[deploy] starting worker $i/$AIO_MAX_TOTAL: $name ..."
        docker run -d \
            --name "$name" \
            --label "wisepen.role=aio-worker" \
            --security-opt seccomp=unconfined \
            --shm-size 2gb \
            --restart unless-stopped \
            -v "${AIO_WORKSPACE_CACHE}:${AIO_WORKSPACE_CACHE}" \
            "$IMAGE"
    done
    echo "[deploy] $AIO_MAX_TOTAL workers started."
}

stop_workers() {
    echo "[deploy] stopping all workers..."
    for i in $(seq 1 "$AIO_MAX_TOTAL"); do
        local name="${AIO_CONTAINER_NAME}-${i}"
        if docker inspect "$name" >/dev/null 2>&1; then
            docker rm -f "$name" && echo "  removed $name"
        fi
    done
    echo "[deploy] all workers stopped."
}

status() {
    echo "=== AIO Worker Status ==="
    for i in $(seq 1 "$AIO_MAX_TOTAL"); do
        local name="${AIO_CONTAINER_NAME}-${i}"
        if docker inspect "$name" >/dev/null 2>&1; then
            local state=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null)
            echo "  $name: $state"
        else
            echo "  $name: not created"
        fi
    done
    echo ""
    echo "image: $IMAGE"
    echo "workspace cache: $AIO_WORKSPACE_CACHE"
    echo "env for sandbox service:"
    echo "  SANDBOX_QUEUE_ENABLE=1"
    echo "  AIO_WORKER_MIN_IDLE=$AIO_MIN_IDLE"
    echo "  AIO_WORKER_MAX_TOTAL=$AIO_MAX_TOTAL"
    echo "  AIO_WORKSPACE_CACHE_DIR=$AIO_WORKSPACE_CACHE"
    echo "  AIO_WORKER_IMAGE=$IMAGE"
}

# ---- 主入口 ----

case "${1:-start}" in
    start)
        pull_image
        start_workers
        echo ""
        echo "[deploy] ready. Start sandbox service with:"
        echo "  export SANDBOX_QUEUE_ENABLE=1"
        echo "  export AIO_WORKER_MIN_IDLE=$AIO_MIN_IDLE"
        echo "  export AIO_WORKER_MAX_TOTAL=$AIO_MAX_TOTAL"
        echo "  export AIO_WORKSPACE_CACHE_DIR=$AIO_WORKSPACE_CACHE"
        echo "  export AIO_WORKER_IMAGE=$IMAGE"
        echo "  cd AI && python -m sandbox.transport.http.server"
        ;;
    stop)
        stop_workers
        ;;
    restart)
        stop_workers
        start_workers
        ;;
    status)
        status
        ;;
    *)
        echo "usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
