# ============================================================
# 一键启动: AIO Worker 容器 + 沙箱后端 + aio-gateway
# ============================================================

param(
    [switch]$SkipDocker,           # 跳过容器创建（已手动启动则用此参数）
    [int]$Workers = 2,            # 预启动容器数
    [string]$Image = "ghcr.io/agent-infra/sandbox:latest",
    [string]$WorkspaceCache = "C:\workspaces",
    [int]$SandboxPort = 9001,
    [int]$GatewayPort = 8001
)

$ErrorActionPreference = "Stop"

# ---- 1. AIO Worker 容器 ----
if (-not $SkipDocker) {
    Write-Host "[1/3] Starting AIO worker containers..." -ForegroundColor Cyan
    for ($i = 1; $i -le $Workers; $i++) {
        $name = "sandbox-workspace-$i"
        $exists = docker inspect $name 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  $name exists, reusing."
            docker start $name 2>$null
        } else {
            Write-Host "  creating $name..."
            docker run -d --name $name --security-opt seccomp=unconfined --shm-size 2gb --restart unless-stopped -v "${WorkspaceCache}:${WorkspaceCache}" $Image
        }
    }
    Write-Host "  workers ready." -ForegroundColor Green
}

# ---- 2. 沙箱后端 (sandbox-service) ----
Write-Host "[2/3] Starting sandbox backend on :$SandboxPort..." -ForegroundColor Cyan
$env:SANDBOX_QUEUE_ENABLE = "1"
$env:AIO_WORKER_MIN_IDLE = [string]$Workers
$env:AIO_WORKER_MAX_TOTAL = "8"
$env:AIO_WORKSPACE_CACHE_DIR = $WorkspaceCache
$env:AIO_WORKER_IMAGE = $Image
$env:SANDBOX_PORT = [string]$SandboxPort

$sandboxJob = Start-Job -Name "sandbox" -ScriptBlock {
    Set-Location $using:PWD
    python -m sandbox.transport.http.server
}
Write-Host "  sandbox backend started (PID $($sandboxJob.Id))." -ForegroundColor Green

Start-Sleep 3

# ---- 3. aio-gateway ----
Write-Host "[3/3] Starting aio-gateway on :$GatewayPort..." -ForegroundColor Cyan
$env:AIO_WORKER_IMAGE = $Image
$env:AIO_WORKER_MIN_IDLE = [string]$Workers
$env:AIO_WORKER_MAX_TOTAL = "8"
$env:AIO_WORKSPACE_CACHE_DIR = $WorkspaceCache

uv run uvicorn aio_gateway.main:app --host 127.0.0.1 --port $GatewayPort

# ---- 清理 ----
Write-Host "`nShutting down..." -ForegroundColor Yellow
Stop-Job -Name "sandbox" -ErrorAction SilentlyContinue
Remove-Job -Name "sandbox" -Force -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green
