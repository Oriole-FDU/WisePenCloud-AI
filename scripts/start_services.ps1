$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$rootDir = "D:\WisePenCloud-AI\wisepen_common\WisePenCloud-AI"

$services = @(
    @{
        Name = "WisePen Chat Service"
        Path = "$rootDir\services\wisepen-chat-service\src\chat"
        Cmd  = "uv run python main.py"
    },
    @{
        Name = "WisePen MCP Service"
        Path = "$rootDir\services\wisepen-mcp-service\src\wisepen_mcp"
        Cmd  = "uv run python main.py"
    },
    @{
        Name = "WisePen Scholar Service"
        Path = "$rootDir\services\wisepen-scholar-service\src\scholar"
        Cmd  = "uv run python main.py"
    },
    @{
        Name = "WisePen View Frontend"
        Path = "$rootDir\WisePenView"
        Cmd  = "npm run dev"
    }
)

foreach ($s in $services) {
    $command = "`$host.UI.RawUI.WindowTitle = '$($s.Name)'; Write-Host '=== $($s.Name) ===' -ForegroundColor Green; $($s.Cmd)"
    $argList = @("-NoExit", "-Command", $command)
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -WorkingDirectory $s.Path -WindowStyle Normal
}
