[CmdletBinding()]
param(
    [ValidateSet("backend", "e2e", "all")]
    [string]$Suite = "all",
    [string]$Tests,
    [string]$DatabaseUrl = "postgresql+psycopg://supplier_risk_test:test_only_password@postgres-test:5432/supplier_risk_test"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $repoRoot "frontend"
$composeBaseFile = Join-Path $repoRoot "compose.test.yaml"
$composeOverrideFile = Join-Path $repoRoot "compose.hardening-test.yaml"
$dockerfile = Join-Path $repoRoot "Dockerfile"
$projectName = "supplier-risk-hardening-test"
$baseUrl = "http://127.0.0.1:18080"
$expectedDatabaseUrl = "postgresql+psycopg://supplier_risk_test:test_only_password@postgres-test:5432/supplier_risk_test"
$runId = "{0}-{1}" -f ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), [Guid]::NewGuid().ToString("N").Substring(0, 8)
$imageReference = "supplierriskmonitoring-hardening-test:$runId"
$evidenceRoot = Join-Path $repoRoot ".omo/evidence/task-1-current-version-hardening"
$evidenceDirectory = Join-Path $evidenceRoot $runId
$summaryPath = Join-Path $evidenceDirectory "summary.json"
$buildLogPath = Join-Path $evidenceDirectory "build.txt"
$compose = @(
    "compose", "--project-name", $projectName,
    "--file", $composeBaseFile,
    "--file", $composeOverrideFile
)

function Stop-WithValidationError {
    param([Parameter(Mandatory)][string]$Message)

    [Console]::Error.WriteLine($Message)
    exit 2
}

function Resolve-AllowedTests {
    param(
        [Parameter(Mandatory)][string]$SelectedSuite,
        [AllowNull()][string]$RequestedTests
    )

    if ($SelectedSuite -eq "all") {
        if (-not [string]::IsNullOrWhiteSpace($RequestedTests)) {
            Stop-WithValidationError "Suite=all 固定运行全量测试，不接受 Tests 参数。"
        }
        return @()
    }

    if ([string]::IsNullOrWhiteSpace($RequestedTests)) {
        if ($SelectedSuite -eq "backend") {
            return @("tests")
        }
        return @("tests/e2e")
    }

    $allowed = [System.Collections.Generic.List[string]]::new()
    foreach ($rawPath in $RequestedTests.Split(',')) {
        $path = $rawPath.Trim()
        if (
            [string]::IsNullOrWhiteSpace($path) -or
            [System.IO.Path]::IsPathRooted($path) -or
            $path.Contains("\") -or
            $path.Split('/') -contains ".."
        ) {
            Stop-WithValidationError "Tests 仅接受白名单相对测试路径：$rawPath"
        }

        if ($SelectedSuite -eq "backend") {
            $matchesAllowlist = $path -eq "tests" -or $path -match '^tests/test_[A-Za-z0-9_]+\.py$'
            $root = Join-Path $repoRoot "backend"
        }
        else {
            $matchesAllowlist = $path -eq "tests/e2e" -or $path -match '^tests/e2e/[A-Za-z0-9_.-]+\.spec\.ts$'
            $root = $frontendRoot
        }
        if (-not $matchesAllowlist) {
            Stop-WithValidationError "Tests 路径不在 $SelectedSuite 白名单：$path"
        }

        $candidate = Join-Path $root $path
        $expectedType = if ($path -in @("tests", "tests/e2e")) { "Container" } else { "Leaf" }
        if (-not (Test-Path -LiteralPath $candidate -PathType $expectedType)) {
            Stop-WithValidationError "Tests 路径不存在或类型不符：$path"
        }
        $allowed.Add($path)
    }
    if ($allowed.Count -eq 0) {
        Stop-WithValidationError "Tests 至少包含一个白名单测试路径。"
    }
    return $allowed.ToArray()
}

function Test-PortAvailable {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        18080
    )
    try {
        $listener.Start()
    }
    catch [System.Net.Sockets.SocketException] {
        Stop-WithValidationError "拒绝运行：127.0.0.1:18080 已被占用，不会停止现有进程。"
    }
    finally {
        $listener.Stop()
    }
}

function Invoke-DockerQuery {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = @(& docker @Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 资源查询失败（exit $LASTEXITCODE）：docker $([string]::Join(' ', $Arguments))"
    }
    return @($output | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -ne "" })
}

function Get-ProjectResources {
    $label = "label=com.docker.compose.project=$projectName"
    return [ordered]@{
        containers = @(Invoke-DockerQuery -Arguments @("ps", "--all", "--quiet", "--filter", $label))
        networks = @(Invoke-DockerQuery -Arguments @("network", "ls", "--quiet", "--filter", $label))
        volumes = @(Invoke-DockerQuery -Arguments @("volume", "ls", "--quiet", "--filter", $label))
    }
}

function Get-OwnedResources {
    $projectLabel = "label=com.docker.compose.project=$projectName"
    $ownerLabel = "label=supplier-risk-hardening.owner=$runId"
    return [ordered]@{
        containers = @(Invoke-DockerQuery -Arguments @("ps", "--all", "--quiet", "--filter", $projectLabel, "--filter", $ownerLabel))
        networks = @(Invoke-DockerQuery -Arguments @("network", "ls", "--quiet", "--filter", $projectLabel, "--filter", $ownerLabel))
        volumes = @(Invoke-DockerQuery -Arguments @("volume", "ls", "--quiet", "--filter", $projectLabel, "--filter", $ownerLabel))
    }
}

function Get-ResourceCount {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Resources)
    return @($Resources.containers).Count + @($Resources.networks).Count + @($Resources.volumes).Count
}

function Assert-NoProjectResources {
    $resources = Get-ProjectResources
    if ((Get-ResourceCount -Resources $resources) -gt 0) {
        Stop-WithValidationError "拒绝运行：Compose 项目 supplier-risk-hardening-test 已有资源；不会复用、覆盖或清理。"
    }
}

function Get-SourceFingerprint {
    $relativePaths = @(& git -c core.quotepath=false ls-files --cached --others --exclude-standard -- Dockerfile backend frontend)
    if ($LASTEXITCODE -ne 0 -or $relativePaths.Count -eq 0) {
        throw "无法生成当前 Dockerfile 构建源文件清单。"
    }
    $lines = foreach ($relativePath in @($relativePaths | Sort-Object -Unique)) {
        $absolutePath = Join-Path $repoRoot $relativePath
        if (Test-Path -LiteralPath $absolutePath -PathType Leaf) {
            $fileHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $absolutePath).Hash.ToLowerInvariant()
            "$relativePath`t$fileHash"
        }
    }
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes([string]::Join("`n", $lines))
    return [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}

function Invoke-LoggedNative {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][scriptblock]$Command
    )

    & $Command *> $LogPath
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw [System.ComponentModel.Win32Exception]::new($code, "$Name 失败（exit $code）；详见 $LogPath。")
    }
}

function Read-JUnitCounts {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "测试命令未生成 JUnit：$Path"
    }
    try {
        [xml]$document = Get-Content -Raw -LiteralPath $Path
    }
    catch {
        throw "JUnit 报告无法解析：$Path ($($_.Exception.Message))"
    }
    $cases = @($document.SelectNodes("//testcase"))
    $failures = @($document.SelectNodes("//testcase/failure | //testcase/error"))
    $skipped = @($document.SelectNodes("//testcase/skipped"))
    return [ordered]@{
        tests = $cases.Count
        failures = $failures.Count
        skipped = $skipped.Count
        passed = ($cases.Count -gt 0) -and ($failures.Count -eq 0)
    }
}

function Stop-OwnedStack {
    $projectResources = Get-ProjectResources
    if ((Get-ResourceCount -Resources $projectResources) -eq 0) { return }

    $ownedResources = Get-OwnedResources
    foreach ($kind in @("containers", "networks", "volumes")) {
        $foreign = @($projectResources[$kind] | Where-Object { $_ -notin @($ownedResources[$kind]) })
        if ($foreign.Count -gt 0) {
            throw "检测到不属于本轮的 $kind，拒绝执行 Compose down。"
        }
    }
    if ((Get-ResourceCount -Resources $ownedResources) -eq 0) {
        throw "Compose 项目存在资源但没有本轮 owner label，拒绝清理。"
    }

    & docker @compose --profile tools down --volumes --remove-orphans *>> (Join-Path $evidenceDirectory "cleanup.txt")
    if ($LASTEXITCODE -ne 0) { throw "本轮 Compose 资源清理失败（exit $LASTEXITCODE）。" }
    $residual = Get-ProjectResources
    if ((Get-ResourceCount -Resources $residual) -ne 0) {
        throw "本轮 Compose 资源清理后仍有残留。"
    }
}

if ($DatabaseUrl -cne $expectedDatabaseUrl) {
    Stop-WithValidationError "拒绝运行：DATABASE_URL 必须精确指向隔离 supplier_risk_test。"
}
$selectedTests = @(Resolve-AllowedTests -SelectedSuite $Suite -RequestedTests $Tests)
foreach ($requiredPath in @($frontendRoot, $composeBaseFile, $composeOverrideFile, $dockerfile)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        Stop-WithValidationError "缺少验收入口所需路径：$requiredPath"
    }
}
Test-PortAvailable
Assert-NoProjectResources

New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null
$sourceFingerprint = Get-SourceFingerprint
$previousEnvironment = @{}
foreach ($name in @(
    "TEST_DATABASE_URL", "HARDENING_TEST_IMAGE", "HARDENING_RUN_ID",
    "HARDENING_EVIDENCE_DIR", "HARDENING_SOURCE_FINGERPRINT", "PLAYWRIGHT_BASE_URL"
)) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$env:TEST_DATABASE_URL = $expectedDatabaseUrl
$env:HARDENING_TEST_IMAGE = $imageReference
$env:HARDENING_RUN_ID = $runId
$env:HARDENING_EVIDENCE_DIR = $evidenceDirectory
$env:HARDENING_SOURCE_FINGERPRINT = $sourceFingerprint

$exitCode = 1
$resourceCreationAttempted = $false
$imageBuilt = $false
$imageIdentityVerified = $false
$summary = [ordered]@{
    schema = "supplier-risk-monitoring/current-version-hardening-run/v1"
    status = "failed"
    suite = $Suite
    requested_tests = $selectedTests
    run_id = $runId
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    project = $projectName
    database = "supplier_risk_test"
    base_url = $baseUrl
    source_sha256 = $sourceFingerprint
    image = [ordered]@{reference = $imageReference; id = $null; source_label = $null}
    phases = [ordered]@{}
    cleanup = [ordered]@{attempted = $false; passed = $false; image_removed = $false}
    evidence_directory = $evidenceDirectory
    error = $null
}

try {
    Invoke-LoggedNative -Name "当前 Dockerfile 镜像构建" -LogPath $buildLogPath -Command {
        & docker build --tag $imageReference `
            --label "supplier-risk-hardening.source_sha256=$sourceFingerprint" `
            --label "supplier-risk-hardening.owner=$runId" `
            --file $dockerfile $repoRoot
    }
    $imageBuilt = $true
    $imageId = ((@(& docker image inspect --format '{{.Id}}' $imageReference)) -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($imageId)) {
        throw "无法读取本轮构建镜像 ID。"
    }
    $imageSource = ((@(& docker image inspect --format '{{ index .Config.Labels "supplier-risk-hardening.source_sha256" }}' $imageReference)) -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or $imageSource -cne $sourceFingerprint) {
        throw "本轮镜像源指纹与当前工作区不一致。"
    }
    $imageOwner = ((@(& docker image inspect --format '{{ index .Config.Labels "supplier-risk-hardening.owner" }}' $imageReference)) -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or $imageOwner -cne $runId) {
        throw "本轮镜像缺少正确 owner label。"
    }
    $imageIdentityVerified = $true
    $summary.image.id = $imageId
    $summary.image.source_label = $imageSource

    if ($Suite -in @("backend", "all")) {
        $backendStarted = [DateTimeOffset]::UtcNow
        $resourceCreationAttempted = $true
        Invoke-LoggedNative -Name "后端隔离栈启动（迁移 + seed）" -LogPath (Join-Path $evidenceDirectory "backend-compose-up.txt") -Command {
            & docker @compose up --detach --wait app-test
        }
        $backendTests = @(if ($Suite -eq "all") { "tests" } else { $selectedTests })
        $junitPath = Join-Path $evidenceDirectory "junit.xml"
        & docker @compose --profile tools run --rm test-runner pytest @backendTests --junitxml=/test-evidence/junit.xml *> (Join-Path $evidenceDirectory "backend-tests.txt")
        $pytestExitCode = $LASTEXITCODE
        $backendCounts = $null
        $junitDiagnostic = "ok"
        $junitError = $null
        if (Test-Path -LiteralPath $junitPath -PathType Leaf) {
            try {
                $backendCounts = Read-JUnitCounts -Path $junitPath
            }
            catch {
                $junitDiagnostic = "unparsable"
                $junitError = $_.Exception.Message
            }
        }
        else {
            $junitDiagnostic = "missing"
            $junitError = "测试命令未生成 JUnit 报告：$junitPath"
        }
        $backendFailed = -not (
            ($pytestExitCode -eq 0) -and
            ($null -ne $backendCounts) -and
            $backendCounts.passed
        )
        $summary.phases.backend = [ordered]@{
            status = if ($backendFailed) { "failed" } else { "passed" }
            started_at_utc = $backendStarted.ToString("o")
            finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            exit_code = $pytestExitCode
            tests = if ($null -ne $backendCounts) { $backendCounts.tests } else { $null }
            failures = if ($null -ne $backendCounts) { $backendCounts.failures } else { $null }
            skipped = if ($null -ne $backendCounts) { $backendCounts.skipped } else { $null }
            junit = $junitPath
            junit_available = ($null -ne $backendCounts)
            junit_diagnostic = $junitDiagnostic
            junit_error = $junitError
        }
        if ($backendFailed) {
            $exitCode = if ($pytestExitCode -ne 0) { $pytestExitCode } else { 1 }
            throw "后端阶段失败：pytest exit=$pytestExitCode；junit=$junitDiagnostic。"
        }
        Stop-OwnedStack
        $resourceCreationAttempted = $false
    }

    if ($Suite -eq "all") {
        $frontendStarted = [DateTimeOffset]::UtcNow
        $frontendJunitPath = Join-Path $evidenceDirectory "frontend-junit.xml"
        $frontendUnitLog = Join-Path $evidenceDirectory "frontend-unit.txt"
        $frontendExitCode = $null
        Push-Location $frontendRoot
        try {
            & npm run test:unit -- --reporter=junit --outputFile=$frontendJunitPath *> $frontendUnitLog
            $frontendExitCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        $frontendCounts = $null
        $frontendJunitDiagnostic = "ok"
        $frontendJunitError = $null
        if (Test-Path -LiteralPath $frontendJunitPath -PathType Leaf) {
            try {
                $frontendCounts = Read-JUnitCounts -Path $frontendJunitPath
            }
            catch {
                $frontendJunitDiagnostic = "unparsable"
                $frontendJunitError = $_.Exception.Message
            }
        }
        else {
            $frontendJunitDiagnostic = "missing"
            $frontendJunitError = "前端单元测试未生成 JUnit 报告：$frontendJunitPath"
        }
        $frontendFailed = -not (
            ($frontendExitCode -eq 0) -and
            ($null -ne $frontendCounts) -and
            $frontendCounts.passed
        )
        $typecheckStatus = "not-run"
        $buildStatus = "not-run"
        $typecheckExit = $null
        $buildExit = $null
        if (-not $frontendFailed) {
            $typecheckLog = Join-Path $evidenceDirectory "frontend-typecheck.txt"
            $buildLog = Join-Path $evidenceDirectory "frontend-build.txt"
            Push-Location $frontendRoot
            try {
                & npm run typecheck *> $typecheckLog
                $typecheckExit = $LASTEXITCODE
                if ($typecheckExit -eq 0) {
                    $typecheckStatus = "passed"
                    & npm run build *> $buildLog
                    $buildExit = $LASTEXITCODE
                    if ($buildExit -eq 0) { $buildStatus = "passed" } else { $buildStatus = "failed" }
                }
                else {
                    $typecheckStatus = "failed"
                }
            }
            finally {
                Pop-Location
            }
        }
        $frontendFailed = $frontendFailed -or ($null -ne $typecheckExit -and $typecheckExit -ne 0) -or ($null -ne $buildExit -and $buildExit -ne 0)
        $summary.phases.frontend = [ordered]@{
            status = if ($frontendFailed) { "failed" } else { "passed" }
            started_at_utc = $frontendStarted.ToString("o")
            finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            exit_code = if ($null -ne $typecheckExit -and $typecheckExit -ne 0) { $typecheckExit } elseif ($null -ne $buildExit -and $buildExit -ne 0) { $buildExit } else { $frontendExitCode }
            tests = if ($null -ne $frontendCounts) { $frontendCounts.tests } else { $null }
            failures = if ($null -ne $frontendCounts) { $frontendCounts.failures } else { $null }
            skipped = if ($null -ne $frontendCounts) { $frontendCounts.skipped } else { $null }
            junit = $frontendJunitPath
            junit_available = ($null -ne $frontendCounts)
            junit_diagnostic = $frontendJunitDiagnostic
            junit_error = $frontendJunitError
            typecheck = $typecheckStatus
            build = $buildStatus
        }
        if ($frontendFailed) {
            $exitCode = if ($null -ne $frontendExitCode -and $frontendExitCode -ne 0) { $frontendExitCode } elseif ($null -ne $typecheckExit -and $typecheckExit -ne 0) { $typecheckExit } elseif ($null -ne $buildExit -and $buildExit -ne 0) { $buildExit } else { 1 }
            throw "前端阶段失败：npm exit=$frontendExitCode；junit=$frontendJunitDiagnostic。"
        }
    }

    if ($Suite -in @("e2e", "all")) {
        Test-PortAvailable
        Assert-NoProjectResources
        $e2eStarted = [DateTimeOffset]::UtcNow
        $resourceCreationAttempted = $true
        Invoke-LoggedNative -Name "E2E 全新隔离栈启动与 seed" -LogPath (Join-Path $evidenceDirectory "e2e-compose-up.txt") -Command {
            & docker @compose up --detach --wait app-test
        }
        $health = Invoke-RestMethod -Uri "$baseUrl/api/v1/system/health"
        if ($health.status -ne "ok" -or $health.database -ne "ok") {
            throw "E2E 隔离栈健康检查未返回数据库可用。"
        }

        $playwrightExecutable = Join-Path $frontendRoot "node_modules/.bin/playwright.cmd"
        if (-not (Test-Path -LiteralPath $playwrightExecutable -PathType Leaf)) {
            throw "Playwright 工具不可用：$playwrightExecutable"
        }
        $playwrightJson = Join-Path $evidenceDirectory "playwright.json"
        $playwrightError = Join-Path $evidenceDirectory "playwright-error.txt"
        $e2eTests = @(if ($Suite -eq "all") { "tests/e2e" } else { $selectedTests })
        $env:PLAYWRIGHT_BASE_URL = $baseUrl
        $playwrightExitCode = $null
        Push-Location $frontendRoot
        try {
            & $playwrightExecutable test @e2eTests --workers=1 --reporter=json > $playwrightJson 2> $playwrightError
            $playwrightExitCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        $playwrightReport = $null
        $reportDiagnostic = "ok"
        $reportError = $null
        if (Test-Path -LiteralPath $playwrightJson -PathType Leaf) {
            try {
                $playwrightReport = Get-Content -Raw -LiteralPath $playwrightJson | ConvertFrom-Json
            }
            catch {
                $reportDiagnostic = "unparsable"
                $reportError = $_.Exception.Message
            }
        }
        else {
            $reportDiagnostic = "missing"
            $reportError = "Playwright 未生成机器可读报告：$playwrightJson"
        }
        $statsProp = if ($null -ne $playwrightReport) { $playwrightReport.psobject.Properties['stats'] } else { $null }
        $statsValid = ($null -ne $statsProp)
        if ($null -ne $playwrightReport -and -not $statsValid) {
            $reportDiagnostic = "invalid"
            $reportError = "Playwright 报告可解析但缺少 stats 字段：$playwrightJson"
        }
        $playwrightCount = if ($statsValid) {
            [int]$playwrightReport.stats.expected + [int]$playwrightReport.stats.unexpected + [int]$playwrightReport.stats.flaky + [int]$playwrightReport.stats.skipped
        }
        else {
            $null
        }
        $e2eFailed = -not (
            ($playwrightExitCode -eq 0) -and
            $statsValid -and
            ([int]$playwrightReport.stats.unexpected -eq 0) -and
            ($playwrightCount -gt 0)
        )
        $summary.phases.e2e = [ordered]@{
            status = if ($e2eFailed) { "failed" } else { "passed" }
            started_at_utc = $e2eStarted.ToString("o")
            finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            exit_code = if ($null -ne $playwrightExitCode) { $playwrightExitCode } else { $null }
            tests = $playwrightCount
            failures = if ($statsValid) { [int]$playwrightReport.stats.unexpected } else { $null }
            skipped = if ($statsValid) { [int]$playwrightReport.stats.skipped } else { $null }
            report = $playwrightJson
            report_available = ($null -ne $playwrightReport)
            report_diagnostic = $reportDiagnostic
            report_error = $reportError
            database_recreated_and_seeded = $true
        }
        if ($e2eFailed) {
            $exitCode = if ($null -ne $playwrightExitCode -and $playwrightExitCode -ne 0) { $playwrightExitCode } else { 1 }
            throw "E2E 阶段失败：playwright exit=$playwrightExitCode；report=$reportDiagnostic。"
        }
        Stop-OwnedStack
        $resourceCreationAttempted = $false
    }

    $exitCode = 0
}
catch {
    if ($exitCode -eq 1 -and $_.Exception -is [System.ComponentModel.Win32Exception]) {
        $exitCode = $_.Exception.NativeErrorCode
    }
    $summary.error = $_.Exception.Message
    [Console]::Error.WriteLine($_.Exception.Message)
}
finally {
    if ($resourceCreationAttempted) {
        $summary.cleanup.attempted = $true
        try {
            Stop-OwnedStack
            $summary.cleanup.passed = $true
        }
        catch {
            $summary.error = "{0} 清理错误：{1}" -f $summary.error, $_.Exception.Message
            [Console]::Error.WriteLine($_.Exception.Message)
            $exitCode = 1
        }
    }
    else {
        $summary.cleanup.passed = $true
    }

    if ($imageBuilt -and $imageIdentityVerified) {
        & docker image rm $imageReference *>> (Join-Path $evidenceDirectory "cleanup.txt")
        if ($LASTEXITCODE -eq 0) {
            $summary.cleanup.image_removed = $true
        }
        else {
            $summary.error = "{0} 本轮临时镜像清理失败。" -f $summary.error
            $exitCode = 1
        }
    }
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
    $summary.status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    $summary.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $json = $summary | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText(
        $summaryPath,
        "$json$([Environment]::NewLine)",
        [System.Text.UTF8Encoding]::new($false)
    )
    [Console]::Out.WriteLine($json)
}

exit $exitCode
