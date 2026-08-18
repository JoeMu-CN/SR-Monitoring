[CmdletBinding()]
param(
    [ValidateSet("topic_source_discovery", "controlled_single_page", "local_lifecycle_test")]
    [string]$Mode = "topic_source_discovery",
    [ValidateSet("legacy", "langgraph")]
    [string]$Orchestrator = "langgraph",
    [switch]$EnableCrawl4AI,
    [switch]$Build,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$environmentNames = @(
    "RESEARCH_WORKER_ENABLED",
    "RESEARCH_WORKER_MODE",
    "RESEARCH_ORCHESTRATOR",
    "RESEARCH_CRAWL4AI_ENABLED"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

$exitCode = 0
Push-Location $repoRoot
try {
    # 只为当前 Compose 子进程覆盖本地 Worker 开关，不修改 .env，也不输出敏感配置。
    $env:RESEARCH_WORKER_ENABLED = "true"
    $env:RESEARCH_WORKER_MODE = $Mode
    $env:RESEARCH_ORCHESTRATOR = $Orchestrator
    if ($EnableCrawl4AI) {
        if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "deploy/.env.stage0"))) {
            throw "启用 Crawl4AI 前请先准备 deploy/.env.stage0；脚本不会自动生成或输出 Token。"
        }
        $env:RESEARCH_CRAWL4AI_ENABLED = "true"
    }

    $composeArgs = @("compose")
    if ($EnableCrawl4AI) {
        $composeArgs += @("--env-file", "deploy/.env.stage0", "-f", "compose.yaml", "-f", "compose.stage0.yaml")
    }
    $composeArgs += @("--profile", "research-local-test", "up")
    if ($Detached) {
        $composeArgs += "-d"
    }
    if ($Build) {
        $composeArgs += "--build"
    }
    # 启用浏览器回退时必须把 Crawl4AI 纳入同一次 Compose 编排；
    # 否则只启动 research-worker 会导致内部 DNS 可解析但没有服务监听。
    if ($EnableCrawl4AI) {
        $composeArgs += @("crawl4ai", "research-worker")
    } else {
        $composeArgs += "research-worker"
    }

    Write-Host "启动本地 research-worker：mode=$Mode orchestrator=$Orchestrator"
    & docker @composeArgs
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
}

exit $exitCode
