[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$WorkingDirectory = $PWD.Path,
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$ReadyPort = 0,
    [int]$ReadyTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"

# 探测本机 TCP 端口是否可连接：异步连接加超时，探测本身不会挂起；不需要管理员权限。
function Test-TcpPortOpen {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMs = 400
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connectTask = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $connectTask.Wait($TimeoutMs)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

# 把单个参数或路径包成双引号形式，保证含空格的路径和参数可以完整传递。
function ConvertTo-QuotedToken {
    param([string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

# 状态目录固定在系统临时目录：日志与 PID 文件都不进入仓库，也不会被 Git 跟踪。
$stateDir = Join-Path $env:TEMP "supplier-risk-detached"
if (-not (Test-Path -LiteralPath $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}

$log = Join-Path $stateDir "$Name.log"
$err = Join-Path $stateDir "$Name.err.log"
$pidFile = Join-Path $stateDir "$Name.pid"

# 幂等守卫：PID 文件存在且进程仍存活时禁止重复启动；残留但已退出的记录视为陈旧文件并清理。
if (Test-Path -LiteralPath $pidFile) {
    $existingPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $existingPid = 0
    if ([int]::TryParse($existingPidText, [ref]$existingPid) -and $null -ne (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "服务 $Name 已在运行（PID=$existingPid），不重复启动。"
        Write-Host "如需重启，请先执行：.\scripts\stop-detached.ps1 -Name $Name"
        exit 1
    }
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "发现陈旧的 PID 文件（$pidFile），对应进程已不存在，已清理并继续启动。"
}

# 通过 cmd.exe 完成 cd + 重定向 + 启动链路；命令行走 WMI 创建，子进程不继承调用方的 stdout 管道句柄，
# 因此本脚本退出后调用方读取的管道立即到达 EOF，前台调用不会挂住。
$quotedWorkingDirectory = ConvertTo-QuotedToken $WorkingDirectory
$quotedFilePath = ConvertTo-QuotedToken $FilePath
$argumentTokens = @()
foreach ($argument in $ArgumentList) {
    $argumentTokens += ConvertTo-QuotedToken $argument
}

$commandLine = "cmd.exe /c cd /d $quotedWorkingDirectory && $quotedFilePath"
if ($argumentTokens.Count -gt 0) {
    $commandLine += " " + ($argumentTokens -join " ")
}
$commandLine += " > $(ConvertTo-QuotedToken $log) 2> $(ConvertTo-QuotedToken $err)"

$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $commandLine }
if ($result.ReturnValue -ne 0) {
    throw "WMI 创建进程失败：Win32_Process.Create 返回码 $($result.ReturnValue)"
}

$newPid = [int]$result.ProcessId
Set-Content -LiteralPath $pidFile -Value $newPid -Encoding ascii

# 可选就绪探测：ReadyPort 大于 0 时轮询本机 TCP 端口，最长等待 ReadyTimeoutSeconds 秒。
$ready = $false
if ($ReadyPort -gt 0) {
    $deadline = (Get-Date).AddSeconds([Math]::Max($ReadyTimeoutSeconds, 1))
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPortOpen -Port $ReadyPort) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
}

Write-Host "NAME=$Name"
Write-Host "PID=$newPid"
Write-Host "LOG=$log"
Write-Host "ERR=$err"
Write-Host "PIDFILE=$pidFile"
if ($ReadyPort -gt 0) {
    Write-Host "READY=$ready"
}
else {
    Write-Host "READY=skipped"
}

exit 0
