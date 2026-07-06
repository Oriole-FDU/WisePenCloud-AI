# ============================================================
# AIO Worker Container 自动部署脚本 (PowerShell)
#
# 从容器注册表拉取 AIO 镜像并启动工作容器。AIO 代码不提交到仓库，
# 镜像从 ghcr.io 或火山引擎国内镜像站拉取。
#
# 用法:
#   .\deploy-aio-worker.ps1 [start|stop|restart|status]
#   .\deploy-aio-worker.ps1 start -UseCNMirror
#
# 环境变量:
#   $env:AIO_IMAGE                    — 镜像地址
#   $env:AIO_IMAGE_CN                 — 国内镜像
#   $env:AIO_CONTAINER_NAME           — 容器名前缀 (默认 sandbox-workspace)
#   $env:AIO_WORKSPACE_CACHE          — 缓存目录 (Windows 默认 C:\workspaces)
#   $env:AIO_MIN_IDLE                 — 预热数 (默认 2)
#   $env:AIO_MAX_TOTAL                — 最大容器数 (默认 8)
# ============================================================

param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "start",
    [switch]$UseCNMirror
)

$ErrorActionPreference = "Stop"

# ---- 配置 ----
$ImageDefault   = "ghcr.io/agent-infra/sandbox:latest"
$ImageCN        = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
$ContainerName  = if ($env:AIO_CONTAINER_NAME) { $env:AIO_CONTAINER_NAME } else { "sandbox-workspace" }
$WorkspaceCache = if ($env:AIO_WORKSPACE_CACHE) { $env:AIO_WORKSPACE_CACHE } else { "C:\workspaces" }
$MinIdle        = if ($env:AIO_MIN_IDLE)    { [int]$env:AIO_MIN_IDLE }    else { 2 }
$MaxTotal       = if ($env:AIO_MAX_TOTAL)   { [int]$env:AIO_MAX_TOTAL }   else { 8 }

$Image = if ($UseCNMirror -or $env:AIO_USE_CN_MIRROR -eq "1") {
    Write-Host "[deploy] using China mirror: $ImageCN"
    $ImageCN
} else {
    Write-Host "[deploy] using image: $env:AIO_IMAGE"
    if ($env:AIO_IMAGE) { $env:AIO_IMAGE } else { $ImageDefault }
}

# ---- 函数 ----

function Pull-Image {
    Write-Host "[deploy] pulling image $Image ..."
    docker pull $Image
    Write-Host "[deploy] image ready."
}

function Start-Workers {
    Write-Host "[deploy] creating workspace cache dir: $WorkspaceCache"
    New-Item -ItemType Directory -Force -Path $WorkspaceCache | Out-Null

    for ($i = 1; $i -le $MaxTotal; $i++) {
        $name = "${ContainerName}-${i}"
        $exists = docker inspect $name 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[deploy] container $name exists, skipping."
            continue
        }
        Write-Host "[deploy] starting worker $i/$MaxTotal : $name ..."
        docker run -d `
            --name $name `
            --label "wisepen.role=aio-worker" `
            --security-opt seccomp=unconfined `
            --shm-size 2gb `
            --restart unless-stopped `
            -v "${WorkspaceCache}:${WorkspaceCache}" `
            $Image
    }
    Write-Host "[deploy] $MaxTotal workers started."
}

function Stop-Workers {
    Write-Host "[deploy] stopping all workers..."
    for ($i = 1; $i -le $MaxTotal; $i++) {
        $name = "${ContainerName}-${i}"
        $exists = docker inspect $name 2>$null
        if ($LASTEXITCODE -eq 0) {
            docker rm -f $name
            Write-Host "  removed $name"
        }
    }
    Write-Host "[deploy] all workers stopped."
}

function Show-Status {
    Write-Host "=== AIO Worker Status ==="
    for ($i = 1; $i -le $MaxTotal; $i++) {
        $name = "${ContainerName}-${i}"
        $exists = docker inspect $name 2>$null
        if ($LASTEXITCODE -eq 0) {
            $state = docker inspect -f '{{.State.Status}}' $name 2>$null
            Write-Host "  $name : $state"
        } else {
            Write-Host "  $name : not created"
        }
    }
    Write-Host ""
    Write-Host "image: $Image"
    Write-Host "workspace cache: $WorkspaceCache"
    Write-Host ""
    Write-Host "PowerShell env for sandbox service:"
    Write-Host '  $env:SANDBOX_QUEUE_ENABLE = "1"'
    Write-Host "  `$env:AIO_WORKER_MIN_IDLE = `"$MinIdle`""
    Write-Host "  `$env:AIO_WORKER_MAX_TOTAL = `"$MaxTotal`""
    Write-Host "  `$env:AIO_WORKSPACE_CACHE_DIR = `"$WorkspaceCache`""
    Write-Host "  `$env:AIO_WORKER_IMAGE = `"$Image`""
}

# ---- 主入口 ----

switch ($Action) {
    "start" {
        Pull-Image
        Start-Workers
        Write-Host ""
        Write-Host "[deploy] ready. Start sandbox service:"
        Write-Host '  $env:SANDBOX_QUEUE_ENABLE = "1"'
        Write-Host "  `$env:AIO_WORKER_MIN_IDLE = `"$MinIdle`""
        Write-Host "  `$env:AIO_WORKER_MAX_TOTAL = `"$MaxTotal`""
        Write-Host "  `$env:AIO_WORKSPACE_CACHE_DIR = `"$WorkspaceCache`""
        Write-Host "  `$env:AIO_WORKER_IMAGE = `"$Image`""
        Write-Host "  cd AI; python -m sandbox.transport.http.server"
    }
    "stop"  { Stop-Workers }
    "restart" { Stop-Workers; Start-Workers }
    "status" { Show-Status }
}
