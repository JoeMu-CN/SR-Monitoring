param(
    [string]$DatabaseUrl = "postgresql+psycopg://supplier_risk_test:test_only_password@postgres-test:5432/supplier_risk_test"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $repoRoot "frontend"
$composeFile = Join-Path $repoRoot "compose.test.yaml"
$dockerfile = Join-Path $repoRoot "Dockerfile"
$baselineFile = Join-Path $repoRoot ".omo/evidence/task-1-frontend-gap-closure-baseline.json"
$evidenceDirectory = Join-Path $repoRoot ".omo/evidence/task-12-frontend-gap-closure"
$summaryFile = Join-Path $evidenceDirectory "summary.json"
$projectName = "supplier-risk-task12-production"
$imageName = "supplierriskmonitoring-app"
$baseUrl = "http://127.0.0.1:18080"
$allowedDatabaseName = "supplier_risk_test"
$compose = @("compose", "--project-name", $projectName, "--file", $composeFile)
$exitCode = 1
$stackStarted = $false
$cleanupExitCode = $null

$summary = [ordered]@{
    schema = "supplier-risk-monitoring/task-12-production-gate/v1"
    status = "failed"
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    project = $projectName
    base_url = $baseUrl
    database = [ordered]@{name = $null; accepted = $false}
    frozen_hashes = @()
    checks = [ordered]@{
        port = "not-run"
        docker_image = "not-run"
        compose_health = "not-run"
        frontend_unit = "not-run"
        frontend_lint = "not-run"
        frontend_typecheck = "not-run"
        frontend_build = "not-run"
        playwright = [ordered]@{status = "not-run"; passed = $null}
    }
    evidence = [ordered]@{
        directory = $evidenceDirectory
        browser = (Join-Path $evidenceDirectory "browser")
        playwright_output = (Join-Path $evidenceDirectory "playwright-output.txt")
    }
    cleanup = [ordered]@{attempted = $false; exit_code = $null; residual = $null}
    error = $null
}

function Get-ProjectResidualResources {
    param([string]$ComposeProjectName)

    $containers = @(& docker ps --all --quiet --filter "label=com.docker.compose.project=$ComposeProjectName")
    if ($LASTEXITCODE -ne 0) { throw "无法查询专用 Compose 容器残留。" }
    $networks = @(& docker network ls --quiet --filter "label=com.docker.compose.project=$ComposeProjectName")
    if ($LASTEXITCODE -ne 0) { throw "无法查询专用 Compose 网络残留。" }
    $volumes = @(& docker volume ls --quiet --filter "label=com.docker.compose.project=$ComposeProjectName")
    if ($LASTEXITCODE -ne 0) { throw "无法查询专用 Compose 卷残留。" }
    return [ordered]@{containers = $containers; networks = $networks; volumes = $volumes}
}

function Test-PortAvailable {
    param([int]$Port)

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    }
    catch [System.Net.Sockets.SocketException] {
        throw "拒绝运行：127.0.0.1:$Port 已被占用，不会停止任何其他项目。"
    }
    finally {
        $listener.Stop()
    }
}

function Invoke-LoggedCommand {
    param(
        [string]$Name,
        [string]$LogPath,
        [scriptblock]$Command,
        [switch]$Append
    )

    if ($Append) {
        & $Command *>> $LogPath
    }
    else {
        & $Command *> $LogPath
    }
    if ($LASTEXITCODE -ne 0) { throw "$Name 失败；详见 $LogPath。" }
}

try {
    foreach ($requiredPath in @($frontendRoot, $composeFile, $dockerfile, $baselineFile)) {
        if (-not (Test-Path -LiteralPath $requiredPath)) { throw "缺少生产门所需路径：$requiredPath" }
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $evidenceDirectory "browser") | Out-Null

    try {
        $databaseUri = [Uri]$DatabaseUrl
    }
    catch {
        throw "拒绝运行数据库测试：DATABASE_URL 无法解析。"
    }
    $databaseName = $databaseUri.AbsolutePath.TrimStart("/")
    $summary.database.name = $databaseName
    if ($databaseName -ne $allowedDatabaseName) {
        throw "拒绝运行数据库测试：仅允许显式数据库 supplier_risk_test。"
    }
    $summary.database.accepted = $true

    $baseline = Get-Content -Raw -LiteralPath $baselineFile | ConvertFrom-Json
    $frozenResults = foreach ($frozenPath in @(
        "DESIGN.md",
        "frontend/src/components/ResearchView.tsx",
        "frontend/src/components/SourceOnboardingAgentView.tsx"
    )) {
        $baselineEntry = $baseline.frozen_files | Where-Object {$_.path -eq $frozenPath}
        if ($null -eq $baselineEntry) { throw "冻结文件基线缺失：$frozenPath" }
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $repoRoot $frozenPath)).Hash
        [pscustomobject]@{
            path = $frozenPath
            expected = $baselineEntry.sha256
            actual = $actualHash
            matches = $actualHash -eq $baselineEntry.sha256
        }
    }
    $summary.frozen_hashes = @($frozenResults)
    if (@($frozenResults | Where-Object {-not $_.matches}).Count -gt 0) {
        throw "冻结文件 SHA-256 与 Task 1 基线不一致。"
    }

    Test-PortAvailable -Port 18080
    $summary.checks.port = "passed"
    $residualBeforeStart = Get-ProjectResidualResources -ComposeProjectName $projectName
    if ($residualBeforeStart.containers.Count -gt 0 -or $residualBeforeStart.networks.Count -gt 0 -or $residualBeforeStart.volumes.Count -gt 0) {
        throw "拒绝运行：专用 Compose 项目存在残留，避免影响其他执行。"
    }

    Invoke-LoggedCommand -Name "Dockerfile 生产镜像构建" -LogPath (Join-Path $evidenceDirectory "docker-build-output.txt") -Command {
        & docker build --tag $imageName --file $dockerfile $repoRoot
    }
    $summary.checks.docker_image = "passed"

    $env:TEST_DATABASE_URL = $DatabaseUrl
    $stackStarted = $true
    Invoke-LoggedCommand -Name "隔离 Compose 栈启动" -LogPath (Join-Path $evidenceDirectory "compose-up-output.txt") -Command {
        & docker @compose up --detach --wait app-test
    }
    $health = Invoke-RestMethod -Uri "$baseUrl/api/v1/system/health"
    if ($health.status -ne "ok" -or $health.database -ne "ok") {
        throw "生产容器健康检查未返回数据库可用状态。"
    }
    $summary.checks.compose_health = "passed"

    $previousPlaywrightBaseUrl = [Environment]::GetEnvironmentVariable("PLAYWRIGHT_BASE_URL", "Process")
    Push-Location $frontendRoot
    try {
        Invoke-LoggedCommand -Name "前端 unit 测试" -LogPath (Join-Path $evidenceDirectory "frontend-unit-output.txt") -Command { & npm run test:unit }
        $summary.checks.frontend_unit = "passed"
        Invoke-LoggedCommand -Name "前端 lint" -LogPath (Join-Path $evidenceDirectory "frontend-lint-output.txt") -Command { & npm run lint }
        $summary.checks.frontend_lint = "passed"
        Invoke-LoggedCommand -Name "前端 typecheck" -LogPath (Join-Path $evidenceDirectory "frontend-typecheck-output.txt") -Command { & npm run typecheck }
        $summary.checks.frontend_typecheck = "passed"
        Invoke-LoggedCommand -Name "前端 production build" -LogPath (Join-Path $evidenceDirectory "frontend-build-output.txt") -Command { & npm run build }
        $summary.checks.frontend_build = "passed"

        $env:PLAYWRIGHT_BASE_URL = $baseUrl
        $playwrightLog = Join-Path $evidenceDirectory "playwright-output.txt"
        $playwrightExitCode = 0
        & npm run test:e2e -- tests/e2e/isolated-stack.spec.ts tests/e2e/task-7-risk-detail.spec.ts tests/e2e/task-8-source-signals.spec.ts tests/e2e/task-9-suppliers.spec.ts tests/e2e/todo-4-routes.spec.ts --workers=1 *> $playwrightLog
        if ($LASTEXITCODE -ne 0) { $playwrightExitCode = $LASTEXITCODE }
        & npm run test:e2e -- tests/e2e/task-10-supplier-import.spec.ts tests/e2e/task-11-user-management.spec.ts --workers=1 *>> $playwrightLog
        if ($LASTEXITCODE -ne 0) { $playwrightExitCode = $LASTEXITCODE }
        $playwrightOutput = Get-Content -Raw -LiteralPath $playwrightLog
        $passedCount = 0
        foreach ($passedMatch in [regex]::Matches($playwrightOutput, "(?<count>\d+) passed")) {
            $passedCount += [int]$passedMatch.Groups["count"].Value
        }
        $summary.checks.playwright = [ordered]@{
            status = if ($playwrightExitCode -eq 0) { "passed" } else { "failed" }
            passed = if ($passedCount -gt 0) { $passedCount } else { $null }
        }
        if ($playwrightExitCode -ne 0) { throw "Playwright 测试未全部通过；详见 $playwrightLog。" }
    }
    finally {
        if ($null -eq $previousPlaywrightBaseUrl) {
            Remove-Item Env:PLAYWRIGHT_BASE_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:PLAYWRIGHT_BASE_URL = $previousPlaywrightBaseUrl
        }
        Pop-Location
    }

    $exitCode = 0
}
catch {
    $summary.error = $_.Exception.Message
}
finally {
    if ($stackStarted) {
        $summary.cleanup.attempted = $true
        & docker @compose --profile tools down --volumes --remove-orphans *> (Join-Path $evidenceDirectory "cleanup-output.txt")
        $cleanupExitCode = $LASTEXITCODE
        $summary.cleanup.exit_code = $cleanupExitCode
        if ($cleanupExitCode -ne 0) {
            $summary.error = "隔离测试栈清理失败。"
            $exitCode = 1
        }
        else {
            try {
                $residualAfterCleanup = Get-ProjectResidualResources -ComposeProjectName $projectName
                $summary.cleanup.residual = $residualAfterCleanup
                if ($residualAfterCleanup.containers.Count -gt 0 -or $residualAfterCleanup.networks.Count -gt 0 -or $residualAfterCleanup.volumes.Count -gt 0) {
                    $summary.error = "专用 Compose 项目清理后仍有残留。"
                    $exitCode = 1
                }
            }
            catch {
                $summary.error = $_.Exception.Message
                $exitCode = 1
            }
        }
    }
    Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
    $summary.status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    $summary.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $jsonSummary = $summary | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($summaryFile, "$jsonSummary$([Environment]::NewLine)", [System.Text.UTF8Encoding]::new($false))
    [Console]::Out.WriteLine($jsonSummary)
}

exit $exitCode
