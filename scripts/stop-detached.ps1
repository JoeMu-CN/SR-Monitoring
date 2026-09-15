[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Name
)

$ErrorActionPreference = "Stop"

# 与 start-detached.ps1 使用同一状态目录（系统临时目录，不进仓库）。
$stateDir = Join-Path $env:TEMP "supplier-risk-detached"
$pidFile = Join-Path $stateDir "$Name.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "未找到 $Name 的 PID 文件（$pidFile），没有需要停止的进程。"
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$targetPid = 0
if (-not [int]::TryParse($pidText, [ref]$targetPid)) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "PID 文件内容无效（原始内容：$pidText），已删除 $pidFile；未执行终止操作。"
    exit 0
}

# 必须终止整棵进程树：启动链路是 cmd.exe → node.exe → cmd.exe → node.exe 等多级子进程，
# 只杀根 PID 会留下真正监听端口的后代进程（Stop-Process 做不到，taskkill /T 可以）。
$taskkillOutput = & taskkill.exe /T /F /PID $targetPid 2>&1
$taskkillExitCode = $LASTEXITCODE
foreach ($line in $taskkillOutput) {
    Write-Host $line
}

# 等待进程实际退出（最多约 5 秒），避免 taskkill 尚未回收就误判。
$deadline = (Get-Date).AddSeconds(5)
while ((Get-Date) -lt $deadline) {
    if ($null -eq (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
        break
    }
    Start-Sleep -Milliseconds 200
}

if ($null -ne (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
    Write-Host "停止 $Name 失败：PID=$targetPid 仍存活（taskkill 退出码 $taskkillExitCode），PID 文件保留：$pidFile"
    exit 1
}

Remove-Item -LiteralPath $pidFile -Force
if ($taskkillExitCode -eq 0) {
    Write-Host "已停止 $Name：PID=$targetPid 进程树已终止，进程已不存在，PID 文件已删除（$pidFile）。"
}
else {
    Write-Host "PID=$targetPid 已不存在（taskkill 退出码 $taskkillExitCode），已清理 PID 文件（$pidFile）。"
}

exit 0
