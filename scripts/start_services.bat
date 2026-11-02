@echo off
set "ROOT=D:\WisePenCloud-AI\wisepen_common\WisePenCloud-AI"

start "WisePen Chat Service" /d "%ROOT%\services\wisepen-chat-service\src\chat" powershell -NoExit -ExecutionPolicy Bypass -Command "$host.UI.RawUI.WindowTitle = 'WisePen Chat Service'; Write-Host '=== WisePen Chat Service ===' -ForegroundColor Green; uv run python main.py"

start "WisePen MCP Service" /d "%ROOT%\services\wisepen-mcp-service\src\wisepen_mcp" powershell -NoExit -ExecutionPolicy Bypass -Command "$host.UI.RawUI.WindowTitle = 'WisePen MCP Service'; Write-Host '=== WisePen MCP Service ===' -ForegroundColor Green; uv run python main.py"

start "WisePen Scholar Service" /d "%ROOT%\services\wisepen-scholar-service\src\scholar" powershell -NoExit -ExecutionPolicy Bypass -Command "$host.UI.RawUI.WindowTitle = 'WisePen Scholar Service'; Write-Host '=== WisePen Scholar Service ===' -ForegroundColor Green; uv run python main.py"

start "WisePen View Frontend" /d "%ROOT%\WisePenView" powershell -NoExit -ExecutionPolicy Bypass -Command "$host.UI.RawUI.WindowTitle = 'WisePen View Frontend'; Write-Host '=== WisePen View Frontend ===' -ForegroundColor Cyan; npm run dev"
