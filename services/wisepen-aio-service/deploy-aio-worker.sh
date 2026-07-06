#!/bin/bash
# ============================================================
# AIO Worker Container 自动部署脚本
#
# 从容器注册表拉取 AIO 镜像并启动工作容器。AIO 代码不提交到仓库。
#
# 用法:
#   bash deploy-aio-worker.sh [start|stop|restart|status]
#   AIO_USE_CN_MIRROR=1 bash deploy-aio-worker.sh start
#
# 环境变量:
#   AIO_IMAGE            — 镜像地址
#   AIO_IMAGE_CN         — 国内镜像
#   AIO_USE_CN_MIRROR    — 使用国内镜像 (默认 0)
#   AIO_CONTAINER_NAME   — 容器名前缀 (默认 sandbox-workspace)
#   AIO_WORKSPACE_CACHE  — 缓存目录 (默认 /workspaces)
#   AIO_MIN_IDLE         — 预热数 (默认 2)
#   AIO_MAX_TOTAL        — 最大容器数 (默认 8)
# ============================================================

set -euo pipefail

IMAGE_DEFAULT="ghcr.io/agent-infra/sandbox:latest"
IMAGE_CN="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
CONTAINER_NAME="${AIO_CONTAINER_NAME:-sandbox-workspace}"
WORKSPACE_CACHE="${AIO_WORKSPACE_CACHE:-/workspaces}"
MIN_IDLE="${AIO_MIN_IDLE:-2}"
MAX_TOTAL="${AIO_MAX_TOTAL:-8}"

if [ "${AIO_USE_CN_MIRROR:-0}" = "1" ]; then
    IMAGE="$IMAGE_CN"
    echo "[deploy] using China mirror: $IMAGE"
else
    IMAGE="${AIO_IMAGE:-$IMAGE_DEFAULT}"
    echo "[deploy] using image: $IMAGE"
fi

pull_image() {
    echo "[deploy] pulling image $IMAGE ..."
    docker pull "$IMAGE"
    echo "[deploy] image ready."
}

start_workers() {
    echo "[deploy] creating workspace cache dir: $WORKSPACE_CACHE"
    sudo mkdir -p "$WORKSPACE_CACHE"

    for i in $(seq 1 "$MAX_TOTAL"); do
        local name="${CONTAINER_NAME}-${i}"
        if docker inspect "$name" >/dev/null 2>&1; then
            echo "[deploy] container $name exists, skipping."
            continue
        fi
        echo "[deploy] starting worker $i/$MAX_TOTAL: $name ..."
        docker run -d \
            --name "$name" \
            --label "wisepen.role=aio-worker" \
            --security-opt seccomp=unconfined \
            --shm-size 2gb \
            --restart unless-stopped \
            -v "${WORKSPACE_CACHE}:${WORKSPACE_CACHE}" \
            "$IMAGE"
    done
    echo "[deploy] $MAX_TOTAL workers started."
}

stop_workers() {
    echo "[deploy] stopping all workers..."
    for i in $(seq 1 "$MAX_TOTAL"); do
        local name="${CONTAINER_NAME}-${i}"
        if docker inspect "$name" >/dev/null 2>&1; then
            docker rm -f "$name" && echo "  removed $name"
        fi
    done
    echo "[deploy] all workers stopped."
}

status() {
    echo "=== AIO Worker Status ==="
    for i in $(seq 1 "$MAX_TOTAL"); do
        local name="${CONTAINER_NAME}-${i}"
        if docker inspect "$name" >/dev/null 2>&1; then
            local state=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null)
            echo "  $name: $state"
        else
            echo "  $name: not created"
        fi
    done
    echo "image: $IMAGE"
    echo "workspace cache: $WORKSPACE_CACHE"
    echo "env for sandbox service:"
    echo "  export SANDBOX_QUEUE_ENABLE=1"
    echo "  export AIO_WORKER_MIN_IDLE=$MIN_IDLE"
    echo "  export AIO_WORKER_MAX_TOTAL=$MAX_TOTAL"
    echo "  export AIO_WORKSPACE_CACHE_DIR=$WORKSPACE_CACHE"
    echo "  export AIO_WORKER_IMAGE=$IMAGE"
}

case "${1:-start}" in
    start)   pull_image; start_workers;;
    stop)    stop_workers;;
    restart) stop_workers; start_workers;;
    status)  status;;
    *)       echo "usage: $0 {start|stop|restart|status}"; exit 1;;
esac
