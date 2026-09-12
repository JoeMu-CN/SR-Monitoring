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
$evidenceRoot = Join-Path $repoRoot ".omo/evidence/task-10-current-version-hardening"
$evidenceDirectory = Join-Path $evidenceRoot $runId
$summaryPath = Join-Path $evidenceDirectory "summary.json"
$buildLogPath = Join-Path $evidenceDirectory "build.txt"
$hardeningSeedScript = "tests/seed_hardening_e2e.py"
$baselineBackendTest = "tests/test_e2e_seed.py"
$hardeningBackendTest = "tests/test_hardening_integration.py"
$hardeningE2eSpec = "tests/e2e/current-version-hardening.spec.ts"
$legacySeedNotRunReason = "legacy-baseline-implicit-seed-e2e-only"
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

function Invoke-HardeningSeed {
    <#
        任务10：在隔离栈启动后通过 test-runner 显式叠加 seed_hardening_e2e.py。
        app-test 的容器命令只隐式执行既有 seed_e2e.py；本期新 seed 必须在这里
        额外执行一次，任何非零退出都会原样记录并让调用方立即终止本阶段。
    #>
    param([Parameter(Mandatory)][string]$Phase)

    $seedStarted = [DateTimeOffset]::UtcNow
    $seedLogPath = Join-Path $evidenceDirectory ("hardening-seed-{0}.txt" -f $Phase)
    & docker @compose --profile tools run --rm test-runner python $hardeningSeedScript *> $seedLogPath
    $seedExitCode = $LASTEXITCODE
    return [ordered]@{
        script = $hardeningSeedScript
        status = if ($seedExitCode -eq 0) { "passed" } else { "failed" }
        started_at_utc = $seedStarted.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        exit_code = $seedExitCode
        log = $seedLogPath
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
        passed = ($cases.Count -gt 0) -and ($failures.Count -eq 0) -and ($skipped.Count -eq 0)
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

function New-SeedNotRunRecord {
    <#
        legacy 基线库的 seed 占位记录：app-test 容器命令已隐式执行 seed_e2e.py，
        该库明确不叠加 hardening seed，避免污染 legacy 测试断言。
    #>
    return [ordered]@{
        status = "not-run"
        reason = $legacySeedNotRunReason
    }
}

function New-NotRunStage {
    <#
        fail-fast 语义：首个失败 stage 之后的 stage 一律记录为 not-run，并写明原因。
    #>
    param(
        [Parameter(Mandatory)][string]$Reason,
        [string]$Kind,
        [string]$Spec,
        [bool]$WithHardeningSeed
    )

    $record = [ordered]@{
        status = "not-run"
        reason = $Reason
    }
    if ($Kind) { $record.kind = $Kind }
    if ($Spec) { $record.spec = $Spec }
    $record.database = "supplier_risk_test"
    $record.hardening_seed = $WithHardeningSeed
    $record.seed = New-SeedNotRunRecord
    return $record
}

function Get-StageAggregate {
    <#
        任务10父级聚合：仅汇总已运行且有可解析报告的 stage 计数；not-run stage 不伪造数值。
        父 phase 通过要求至少一个已运行 stage、无失败 stage，且测试数大于 0。
    #>
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Stages)

    $tests = 0
    $failures = 0
    $skipped = 0
    $runCount = 0
    $runFailed = 0
    foreach ($key in @($Stages.Keys)) {
        $stage = $Stages[$key]
        if ($null -eq $stage) { continue }
        $status = [string]$stage["status"]
        if ($status -eq "passed" -or $status -eq "failed") {
            $runCount++
            if ($status -eq "failed") { $runFailed++ }
        }
        if ($stage.Contains("tests") -and $null -ne $stage["tests"]) { $tests += [int]$stage["tests"] }
        if ($stage.Contains("failures") -and $null -ne $stage["failures"]) { $failures += [int]$stage["failures"] }
        if ($stage.Contains("skipped") -and $null -ne $stage["skipped"]) { $skipped += [int]$stage["skipped"] }
    }
    return [ordered]@{
        passed = (($runCount -gt 0) -and ($runFailed -eq 0) -and ($tests -gt 0))
        run_count = $runCount
        run_failed = $runFailed
        tests = $tests
        failures = $failures
        skipped = $skipped
    }
}

function New-BackendNotRunStage {
    <#
        任务10：backend 未运行 stage 的统一记录。定向未选中记 excluded-by-requested-tests；
        前序 stage 失败记 preceding-stage-failed，并保留被选中的路径以便追溯。
    #>
    param(
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Reason,
        [AllowEmptyCollection()][string[]]$SelectedTests,
        [bool]$WithHardeningSeed
    )

    return [ordered]@{
        status = "not-run"
        reason = $Reason
        kind = $Kind
        database = "supplier_risk_test"
        selected_tests = @($SelectedTests)
        hardening_seed = $WithHardeningSeed
        seed = New-SeedNotRunRecord
    }
}

function Invoke-BackendFreshStage {
    <#
        任务10：backend 每个 stage 使用独立 fresh 栈（迁移 + 隐式 seed_e2e）。
        baseline/legacy 绝不叠加 hardening seed；hardening 在其隔离新库恰好叠加一次。
        任何非零退出（含 seed 失败）原样记录，由调用方按固定顺序 fail-fast。
    #>
    param(
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$SelectedTests,
        [Parameter(Mandatory)][string]$JunitFileName,
        [Parameter(Mandatory)][bool]$WithHardeningSeed,
        [Parameter(Mandatory)][string]$SeedPhase
    )

    $stageStarted = [DateTimeOffset]::UtcNow
    $script:resourceCreationAttempted = $true
    $stageFailed = $false
    $stageError = $null
    $stageExitCode = $null
    $stageCounts = $null
    $junitDiagnostic = "ok"
    $junitError = $null
    $seedRecord = $null
    $upLogName = "backend-$Kind-compose-up.txt"
    $testsLogName = "backend-$Kind-tests.txt"
    $junitPath = Join-Path $evidenceDirectory $JunitFileName
    try {
        Invoke-LoggedNative -Name "backend $Kind 全新隔离栈启动（迁移 + seed_e2e）" -LogPath (Join-Path $evidenceDirectory $upLogName) -Command {
            & docker @compose up --detach --wait app-test
        }
        if ($WithHardeningSeed) {
            $seedRecord = Invoke-HardeningSeed -Phase $SeedPhase
            if ($seedRecord.status -ne "passed") {
                $stageFailed = $true
                $stageExitCode = if ($seedRecord.exit_code -ne 0) { $seedRecord.exit_code } else { 1 }
                $stageError = "hardening 阶段 seed 失败：python $hardeningSeedScript exit=$($seedRecord.exit_code)；详见 $($seedRecord.log)。"
            }
        }
        if (-not $stageFailed) {
            & docker @compose --profile tools run --rm test-runner pytest @SelectedTests --junitxml=/test-evidence/$JunitFileName *> (Join-Path $evidenceDirectory $testsLogName)
            $stageExitCode = $LASTEXITCODE
        }
    }
    catch {
        $stageFailed = $true
        $stageError = $_.Exception.Message
        $stageExitCode = if ($_.Exception -is [System.ComponentModel.Win32Exception]) { $_.Exception.NativeErrorCode } else { 1 }
    }
    finally {
        Stop-OwnedStack
        $script:resourceCreationAttempted = $false
    }
    if (-not $stageFailed) {
        if (Test-Path -LiteralPath $junitPath -PathType Leaf) {
            try {
                $stageCounts = Read-JUnitCounts -Path $junitPath
            }
            catch {
                $junitDiagnostic = "unparsable"
                $junitError = $_.Exception.Message
            }
        }
        else {
            $junitDiagnostic = "missing"
            $junitError = "backend $Kind pytest 未生成 JUnit 报告：$junitPath"
        }
        $stageFailed = -not (
            ($stageExitCode -eq 0) -and
            ($null -ne $stageCounts) -and
            $stageCounts.passed
        )
        if ($stageFailed) {
            $stageError = "backend $Kind 阶段失败：pytest exit=$stageExitCode；junit=$junitDiagnostic。"
        }
    }
    return [ordered]@{
        status = if ($stageFailed) { "failed" } else { "passed" }
        kind = $Kind
        database = "supplier_risk_test"
        selected_tests = @($SelectedTests | Where-Object { -not $_.StartsWith("--") })
        hardening_seed = $WithHardeningSeed
        seed = if ($null -ne $seedRecord) { $seedRecord } else { New-SeedNotRunRecord }
        started_at_utc = $stageStarted.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        exit_code = $stageExitCode
        tests = if ($null -ne $stageCounts) { $stageCounts.tests } else { $null }
        failures = if ($null -ne $stageCounts) { $stageCounts.failures } else { $null }
        skipped = if ($null -ne $stageCounts) { $stageCounts.skipped } else { $null }
        junit = $junitPath
        junit_available = ($null -ne $stageCounts)
        junit_diagnostic = $junitDiagnostic
        junit_error = $junitError
        error = $stageError
    }
}

function Get-E2eSpecPaths {
    <#
        任务10：E2E spec 稳定字典序发现（Ordinal），可选按白名单定向过滤后仍保持字典序。
    #>
    param([AllowEmptyCollection()][string[]]$Selected)

    $e2eDirectory = Join-Path $frontendRoot "tests/e2e"
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($item in @(Get-ChildItem -LiteralPath $e2eDirectory -Filter "*.spec.ts" -File)) {
        $names.Add($item.Name)
    }
    $names.Sort([System.StringComparer]::Ordinal)
    $all = @($names | ForEach-Object { "tests/e2e/$_" })
    if ($Selected.Count -eq 0) {
        return $all
    }
    return @($all | Where-Object { $Selected -contains $_ })
}

if ($DatabaseUrl -cne $expectedDatabaseUrl) {
    Stop-WithValidationError "拒绝运行：DATABASE_URL 必须精确指向隔离 supplier_risk_test。"
}
$testsSpecified = -not [string]::IsNullOrWhiteSpace($Tests)
$selectedTests = @(Resolve-AllowedTests -SelectedSuite $Suite -RequestedTests $Tests)
foreach ($requiredPath in @($frontendRoot, $composeBaseFile, $composeOverrideFile, $dockerfile)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        Stop-WithValidationError "缺少验收入口所需路径：$requiredPath"
    }
}

# 任务10拓扑分流：backend 按测试类别拆分为三个独立 fresh 库阶段（固定顺序 baseline → legacy → hardening）。
# - baseline-seed-contract：仅运行 tests/test_e2e_seed.py，保证 seed 确定性断言在未被其它测试污染的新库上执行。
# - legacy：全量 tests 显式排除 baseline 与 hardening 测试；定向时按类别只跑被选中的 legacy 文件。
# - hardening：仅运行 tests/test_hardening_integration.py 并在隔离新库恰好叠加一次 hardening seed。
$baselineBackendArgs = @()
$legacyBackendArgs = @()
$hardeningBackendArgs = @()
if ($Suite -in @("backend", "all")) {
    if ((-not $testsSpecified) -or ($selectedTests -contains "tests")) {
        $baselineBackendArgs = @($baselineBackendTest)
        $legacyBackendArgs = @("tests", "--ignore=$baselineBackendTest", "--ignore=$hardeningBackendTest")
        $hardeningBackendArgs = @($hardeningBackendTest)
    }
    else {
        foreach ($path in $selectedTests) {
            if ($path -eq $baselineBackendTest) {
                $baselineBackendArgs += $path
            }
            elseif ($path -eq $hardeningBackendTest) {
                $hardeningBackendArgs += $path
            }
            else {
                $legacyBackendArgs += $path
            }
        }
    }
}

# 任务10拓扑分流：E2E spec 稳定字典序发现；legacy spec 各自独立基线库，
# current-version-hardening.spec.ts 独立 hardening 库。
$allE2eSpecs = @()
$selectedE2eSpecs = @()
if ($Suite -in @("e2e", "all")) {
    $allE2eSpecs = @(Get-E2eSpecPaths -Selected @())
    if ((-not $testsSpecified) -or ($selectedTests -contains "tests/e2e")) {
        $selectedE2eSpecs = $allE2eSpecs
    }
    else {
        $selectedE2eSpecs = @(Get-E2eSpecPaths -Selected $selectedTests)
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
$notInSuiteReason = "not-in-selected-suite"

function New-PhaseSkeleton {
    param([object]$Reason)

    return [ordered]@{
        status = "not-run"
        reason = $Reason
        started_at_utc = $null
        finished_at_utc = $null
        stages = [ordered]@{}
    }
}

$summary = [ordered]@{
    schema = "supplier-risk-monitoring/current-version-hardening-run/v2"
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
    phases = [ordered]@{
        backend = (New-PhaseSkeleton -Reason $(if ($Suite -in @("backend", "all")) { $null } else { $notInSuiteReason }))
        frontend = (New-PhaseSkeleton -Reason $(if ($Suite -eq "all") { $null } else { $notInSuiteReason }))
        e2e = (New-PhaseSkeleton -Reason $(if ($Suite -in @("e2e", "all")) { $null } else { $notInSuiteReason }))
    }
    cleanup = [ordered]@{attempted = $false; passed = $false; image_removed = $false}
    evidence_directory = $evidenceDirectory
    error = $null
}

if ($Suite -in @("backend", "all")) {
    $summary.phases.backend.stages.baseline = [ordered]@{
        status = "not-run"; reason = "excluded-by-requested-tests"; kind = "baseline"
        database = "supplier_risk_test"; selected_tests = @(); hardening_seed = $false
        seed = (New-SeedNotRunRecord)
    }
    $summary.phases.backend.stages.legacy = [ordered]@{
        status = "not-run"; reason = "excluded-by-requested-tests"; kind = "legacy"
        database = "supplier_risk_test"; selected_tests = @(); hardening_seed = $false
        seed = (New-SeedNotRunRecord)
    }
    $summary.phases.backend.stages.hardening = [ordered]@{
        status = "not-run"; reason = "excluded-by-requested-tests"; kind = "hardening"
        database = "supplier_risk_test"; selected_tests = @(); hardening_seed = $true
        seed = (New-SeedNotRunRecord)
    }
}
foreach ($spec in $allE2eSpecs) {
    $stem = [System.IO.Path]::GetFileName($spec).Replace(".spec.ts", "")
    $isHardeningSpec = ($spec -eq $hardeningE2eSpec)
    $isSelected = ($selectedE2eSpecs -contains $spec)
    $summary.phases.e2e.stages[$stem] = [ordered]@{
        status = "not-run"
        reason = $(if ($isSelected) { $null } else { "excluded-by-requested-tests" })
        kind = if ($isHardeningSpec) { "hardening" } else { "legacy" }
        spec = $spec
        database = "supplier_risk_test"
        hardening_seed = $isHardeningSpec
        seed = (New-SeedNotRunRecord)
    }
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
        $summary.phases.backend.started_at_utc = $backendStarted.ToString("o")
        $backendFailure = $null
        $backendFailureExit = $null
        $backendStop = $false

        # ---------- baseline-seed-contract：fresh 库仅跑 seed 契约测试，绝不叠加 hardening seed ----------
        if ($baselineBackendArgs.Count -gt 0) {
            $summary.phases.backend.stages.baseline = Invoke-BackendFreshStage `
                -Kind "baseline" -SelectedTests $baselineBackendArgs `
                -JunitFileName "junit-backend-baseline.xml" -WithHardeningSeed $false -SeedPhase "backend-baseline"
            if ($summary.phases.backend.stages.baseline.status -eq "failed") {
                $backendStop = $true
                $backendFailure = $summary.phases.backend.stages.baseline.error
                $baselineExit = $summary.phases.backend.stages.baseline.exit_code
                $backendFailureExit = if ($null -ne $baselineExit -and $baselineExit -ne 0) { $baselineExit } else { 1 }
            }
        }

        # ---------- legacy 基线库：fresh 库全量排除 baseline/hardening，绝不叠加 hardening seed ----------
        if ($backendStop) {
            $summary.phases.backend.stages.legacy = New-BackendNotRunStage -Kind "legacy" `
                -Reason "preceding-stage-failed" -SelectedTests $legacyBackendArgs -WithHardeningSeed $false
        }
        elseif ($legacyBackendArgs.Count -gt 0) {
            $summary.phases.backend.stages.legacy = Invoke-BackendFreshStage `
                -Kind "legacy" -SelectedTests $legacyBackendArgs `
                -JunitFileName "junit-backend-legacy.xml" -WithHardeningSeed $false -SeedPhase "backend-legacy"
            if ($summary.phases.backend.stages.legacy.status -eq "failed") {
                $backendStop = $true
                $backendFailure = $summary.phases.backend.stages.legacy.error
                $legacyExit = $summary.phases.backend.stages.legacy.exit_code
                $backendFailureExit = if ($null -ne $legacyExit -and $legacyExit -ne 0) { $legacyExit } else { 1 }
            }
        }
        else {
            $summary.phases.backend.stages.legacy = New-BackendNotRunStage -Kind "legacy" `
                -Reason "excluded-by-requested-tests" -SelectedTests @() -WithHardeningSeed $false
        }

        # ---------- hardening 库：另一个 fresh 库，恰好一次 hardening seed，仅跑集成测试 ----------
        if ($backendStop) {
            $summary.phases.backend.stages.hardening = New-BackendNotRunStage -Kind "hardening" `
                -Reason "preceding-stage-failed" -SelectedTests $hardeningBackendArgs -WithHardeningSeed $true
        }
        elseif ($hardeningBackendArgs.Count -gt 0) {
            $summary.phases.backend.stages.hardening = Invoke-BackendFreshStage `
                -Kind "hardening" -SelectedTests $hardeningBackendArgs `
                -JunitFileName "junit-backend-hardening.xml" -WithHardeningSeed $true -SeedPhase "backend-hardening"
            if ($summary.phases.backend.stages.hardening.status -eq "failed") {
                $backendStop = $true
                $backendFailure = $summary.phases.backend.stages.hardening.error
                $hardeningExit = $summary.phases.backend.stages.hardening.exit_code
                $backendFailureExit = if ($null -ne $hardeningExit -and $hardeningExit -ne 0) { $hardeningExit } else { 1 }
            }
        }
        else {
            $summary.phases.backend.stages.hardening = New-BackendNotRunStage -Kind "hardening" `
                -Reason "excluded-by-requested-tests" -SelectedTests @() -WithHardeningSeed $true
        }

        $backendAggregate = Get-StageAggregate -Stages $summary.phases.backend.stages
        $summary.phases.backend.tests = $backendAggregate.tests
        $summary.phases.backend.failures = $backendAggregate.failures
        $summary.phases.backend.skipped = $backendAggregate.skipped
        $summary.phases.backend.status = if ($backendAggregate.passed) { "passed" } else { "failed" }
        $summary.phases.backend.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        if ($null -ne $backendFailure) {
            $exitCode = if ($null -ne $backendFailureExit) { $backendFailureExit } else { 1 }
            throw $backendFailure
        }
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
            reason = $null
            started_at_utc = $frontendStarted.ToString("o")
            finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            stages = [ordered]@{}
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
        $playwrightExecutable = Join-Path $frontendRoot "node_modules/.bin/playwright.cmd"
        if (-not (Test-Path -LiteralPath $playwrightExecutable -PathType Leaf)) {
            throw "Playwright 工具不可用：$playwrightExecutable"
        }
        $e2eStarted = [DateTimeOffset]::UtcNow
        $summary.phases.e2e.started_at_utc = $e2eStarted.ToString("o")
        $e2eFailure = $null
        $e2eFailureExit = $null
        $e2eStop = $false

        foreach ($spec in $selectedE2eSpecs) {
            $stem = [System.IO.Path]::GetFileName($spec).Replace(".spec.ts", "")
            if ($e2eStop) {
                $summary.phases.e2e.stages[$stem] = New-NotRunStage -Reason "preceding-e2e-stage-failed" `
                    -Kind $(if ($spec -eq $hardeningE2eSpec) { "hardening" } else { "legacy" }) `
                    -Spec $spec -WithHardeningSeed ($spec -eq $hardeningE2eSpec)
                continue
            }

            $isHardeningSpec = ($spec -eq $hardeningE2eSpec)
            $stageStarted = [DateTimeOffset]::UtcNow
            $resourceCreationAttempted = $true
            $stageFailed = $false
            $stageError = $null
            $stageExitCode = $null
            $seedRecord = $null
            $playwrightExitCode = $null
            $playwrightReport = $null
            $statsValid = $false
            $playwrightCount = $null
            $reportDiagnostic = "ok"
            $reportError = $null
            $playwrightJson = Join-Path $evidenceDirectory ("playwright-{0}.json" -f $stem)
            $playwrightError = Join-Path $evidenceDirectory ("playwright-{0}-error.txt" -f $stem)
            $playwrightAttempted = $false
            try {
                Invoke-LoggedNative -Name ("E2E 全新隔离栈启动（{0}）" -f $spec) -LogPath (Join-Path $evidenceDirectory ("e2e-{0}-compose-up.txt" -f $stem)) -Command {
                    & docker @compose up --detach --wait app-test
                }
                $health = Invoke-RestMethod -Uri "$baseUrl/api/v1/system/health"
                if ($health.status -ne "ok" -or $health.database -ne "ok") {
                    throw "E2E 隔离栈健康检查未返回数据库可用。"
                }
                if ($isHardeningSpec) {
                    # 仅 hardening spec 的独立新库叠加恰好一次 hardening seed；legacy spec 保持纯基线。
                    $seedRecord = Invoke-HardeningSeed -Phase "e2e-$stem"
                    if ($seedRecord.status -ne "passed") {
                        $stageFailed = $true
                        $stageExitCode = if ($seedRecord.exit_code -ne 0) { $seedRecord.exit_code } else { 1 }
                        $stageError = "E2E 阶段 seed 失败：python $hardeningSeedScript exit=$($seedRecord.exit_code)；详见 $($seedRecord.log)。"
                    }
                }
                if (-not $stageFailed) {
                    # 每个 spec 独立重建：清理上一 spec 的 trace/截图，避免产物串库。
                    $playwrightArtifactsSource = Join-Path $frontendRoot "test-results"
                    if (Test-Path -LiteralPath $playwrightArtifactsSource) {
                        Remove-Item -LiteralPath $playwrightArtifactsSource -Recurse -Force -ErrorAction SilentlyContinue
                    }
                    $playwrightAttempted = $true
                    $env:PLAYWRIGHT_BASE_URL = $baseUrl
                    Push-Location $frontendRoot
                    try {
                        & $playwrightExecutable test $spec --workers=1 --reporter=json > $playwrightJson 2> $playwrightError
                        $playwrightExitCode = $LASTEXITCODE
                        $stageExitCode = $playwrightExitCode
                    }
                    finally {
                        Pop-Location
                    }
                }
            }
            catch {
                $stageFailed = $true
                $stageError = $_.Exception.Message
                if ($null -eq $stageExitCode) { $stageExitCode = 1 }
            }
            finally {
                # 无论 Playwright 成败都保留本轮 trace/截图等真实产物到任务10证据目录。
                if ($playwrightAttempted) {
                    $playwrightArtifactsTarget = Join-Path $evidenceDirectory ("playwright-artifacts-{0}" -f $stem)
                    $playwrightArtifactsSource = Join-Path $frontendRoot "test-results"
                    if (Test-Path -LiteralPath $playwrightArtifactsSource) {
                        Copy-Item -LiteralPath $playwrightArtifactsSource -Destination $playwrightArtifactsTarget -Recurse -Force -ErrorAction SilentlyContinue
                    }
                }
                Stop-OwnedStack
                $resourceCreationAttempted = $false
            }

            if ($playwrightAttempted -and -not ($stageFailed -and $null -eq $playwrightExitCode)) {
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
                $statsNames = @("expected", "unexpected", "flaky", "skipped")
                $statsValues = @{}
                $statsValid = $false
                $statsProp = if ($null -ne $playwrightReport) { $playwrightReport.psobject.Properties['stats'] } else { $null }
                if ($null -ne $playwrightReport -and $null -ne $statsProp) {
                    $statsValid = $true
                    foreach ($statsName in $statsNames) {
                        $fieldProperty = $statsProp.Value.psobject.Properties[$statsName]
                        if ($null -eq $fieldProperty -or $null -eq $fieldProperty.Value) {
                            $statsValid = $false
                            break
                        }
                        $parsedValue = 0
                        if (-not [int]::TryParse([string]$fieldProperty.Value, [ref]$parsedValue) -or $parsedValue -lt 0) {
                            $statsValid = $false
                            break
                        }
                        $statsValues[$statsName] = $parsedValue
                    }
                }
                if ($null -ne $playwrightReport -and -not $statsValid) {
                    $reportDiagnostic = "invalid"
                    $reportError = "Playwright 报告 stats 字段缺失或非非负整数：$playwrightJson"
                }
                $playwrightCount = if ($statsValid) {
                    [int]$statsValues["expected"] + [int]$statsValues["unexpected"] + [int]$statsValues["flaky"] + [int]$statsValues["skipped"]
                }
                else {
                    $null
                }
                $stageFailed = -not (
                    ($playwrightExitCode -eq 0) -and
                    $statsValid -and
                    ($statsValues["expected"] -gt 0) -and
                    ($statsValues["unexpected"] -eq 0) -and
                    ($statsValues["flaky"] -eq 0) -and
                    ($statsValues["skipped"] -eq 0)
                )
                if ($stageFailed) {
                    $stageError = "E2E 阶段失败：playwright exit=$playwrightExitCode；report=$reportDiagnostic。"
                    $stageExitCode = if ($null -ne $playwrightExitCode -and $playwrightExitCode -ne 0) { $playwrightExitCode } else { 1 }
                }
            }

            $summary.phases.e2e.stages[$stem] = [ordered]@{
                status = if ($stageFailed) { "failed" } else { "passed" }
                reason = $null
                kind = if ($isHardeningSpec) { "hardening" } else { "legacy" }
                spec = $spec
                database = "supplier_risk_test"
                hardening_seed = $isHardeningSpec
                seed = if ($null -ne $seedRecord) { $seedRecord } else { New-SeedNotRunRecord }
                started_at_utc = $stageStarted.ToString("o")
                finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
                exit_code = $stageExitCode
                tests = $playwrightCount
                failures = if ($statsValid) { [int]$statsValues["unexpected"] } else { $null }
                skipped = if ($statsValid) { [int]$statsValues["skipped"] } else { $null }
                report = $playwrightJson
                report_available = ($null -ne $playwrightReport)
                report_diagnostic = $reportDiagnostic
                report_error = $reportError
                database_recreated_and_seeded = $true
                error = $stageError
            }
            if ($stageFailed) {
                # fail-fast：首个失败 spec 后不再为后续 spec 启动新栈，其余记录 not-run。
                $e2eStop = $true
                $e2eFailure = $stageError
                $e2eFailureExit = if ($null -ne $stageExitCode) { $stageExitCode } else { 1 }
            }
        }

        $e2eAggregate = Get-StageAggregate -Stages $summary.phases.e2e.stages
        $summary.phases.e2e.tests = $e2eAggregate.tests
        $summary.phases.e2e.failures = $e2eAggregate.failures
        $summary.phases.e2e.skipped = $e2eAggregate.skipped
        $summary.phases.e2e.status = if ($e2eAggregate.passed) { "passed" } else { "failed" }
        $summary.phases.e2e.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        if ($null -ne $e2eFailure) {
            $exitCode = if ($null -ne $e2eFailureExit) { $e2eFailureExit } else { 1 }
            throw $e2eFailure
        }
    }

    $exitCode = 0
}
catch {
    if ($exitCode -eq 1 -and $_.Exception -is [System.ComponentModel.Win32Exception]) {
        $exitCode = $_.Exception.NativeErrorCode
    }
    $summary.error = $_.Exception.Message
    [Console]::Error.WriteLine($_.Exception.Message)
    foreach ($phaseName in @("backend", "frontend", "e2e")) {
        $phaseRecord = $summary.phases[$phaseName]
        if ($phaseRecord.status -eq "not-run" -and $null -eq $phaseRecord.reason) {
            $phaseRecord.reason = "preceding-phase-failed"
        }
    }
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

    if ($imageBuilt) {
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
