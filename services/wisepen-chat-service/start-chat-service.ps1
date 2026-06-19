param(
    [switch]$NoNewWindow
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$apiCommand = @(
    "run",
    "--project",
    ".",
    "python",
    "-m",
    "chat.main"
)

$workerCommand = @(
    "run",
    "--project",
    ".",
    "arq",
    "chat.workers.web_content_cache_refresh_worker.WorkerSettings"
)

if ($NoNewWindow) {
    Start-Process -FilePath "uv" -ArgumentList $apiCommand -WorkingDirectory $ScriptDir -NoNewWindow
    Start-Process -FilePath "uv" -ArgumentList $workerCommand -WorkingDirectory $ScriptDir -NoNewWindow
} else {
    Start-Process -FilePath "uv" -ArgumentList $apiCommand -WorkingDirectory $ScriptDir
    Start-Process -FilePath "uv" -ArgumentList $workerCommand -WorkingDirectory $ScriptDir
}

Write-Host "Started chat API and web content cache refresh worker from $ScriptDir"
Write-Host "API:    uv run --project . python -m chat.main"
Write-Host "Worker: uv run --project . arq chat.workers.web_content_cache_refresh_worker.WorkerSettings"
