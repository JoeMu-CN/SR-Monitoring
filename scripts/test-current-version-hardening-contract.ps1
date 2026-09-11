[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$entryScript = Join-Path $PSScriptRoot "test-current-version-hardening.ps1"
$contractRunId = "contract-{0}-{1}" -f ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), [Guid]::NewGuid().ToString("N").Substring(0, 8)
# 契约测试自身的证据仍留在任务1目录；入口脚本的证据目录契约由 hardening_evidence_task10 检查锁定。
$contractEvidenceDirectory = Join-Path $repoRoot ".omo/evidence/task-1-current-version-hardening/$contractRunId"
$contractEvidencePath = Join-Path $contractEvidenceDirectory "contract.json"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "supplier-risk-hardening-contract-{0}-{1}" -f $PID, [Guid]::NewGuid().ToString("N")
)
$fakeDockerRoot = Join-Path $temporaryRoot "fake-docker"
$fakeDockerScript = Join-Path $fakeDockerRoot "docker.ps1"
$fakeNpmRoot = Join-Path $temporaryRoot "fake-npm"
$fakeNpmScript = Join-Path $fakeNpmRoot "npm.ps1"
$fakePlaywrightRoot = Join-Path $temporaryRoot "fake-playwright"
$fakePlaywrightScript = Join-Path $fakePlaywrightRoot "playwright.ps1"
$probeEntryScript = Join-Path $PSScriptRoot (".test-current-version-hardening-contract-probe-{0}.ps1" -f [Guid]::NewGuid().ToString("N"))
$frontendProbeEntryScript = Join-Path $PSScriptRoot (".test-current-version-hardening-contract-frontend-{0}.ps1" -f [Guid]::NewGuid().ToString("N"))
$e2eProbeEntryScript = Join-Path $PSScriptRoot (".test-current-version-hardening-contract-e2e-{0}.ps1" -f [Guid]::NewGuid().ToString("N"))
$dockerCallLog = Join-Path $temporaryRoot "docker-calls.jsonl"
$npmCallLog = Join-Path $temporaryRoot "npm-calls.jsonl"
$playwrightCallLog = Join-Path $temporaryRoot "playwright-calls.jsonl"
$stackMarker = Join-Path $temporaryRoot "stack-created.marker"
$originalPath = $env:PATH
$originalMode = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_MODE", "Process")
$originalLog = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_DOCKER_LOG", "Process")
$originalMarker = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_STACK_MARKER", "Process")
$originalNpmLog = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_NPM_LOG", "Process")
$originalPlaywrightLog = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_PLAYWRIGHT_LOG", "Process")
$originalFailSpec = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_FAIL_SPEC", "Process")

$result = [ordered]@{
    schema = "supplier-risk-monitoring/current-version-hardening-contract/v1"
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    checks = [ordered]@{
        unknown_suite = "not-run"
        non_test_database = "not-run"
        path_traversal = "not-run"
        unknown_test = "not-run"
        occupied_port = "not-run"
        occupied_project = "not-run"
        pytest_exit_code = "not-run"
        zero_tests = "not-run"
        successful_nonzero_junit = "not-run"
        cleanup_ownership = "not-run"
        exit_zero_with_failures = "not-run"
        nonzero_with_report = "not-run"
        nonzero_without_report = "not-run"
        frontend_zero = "not-run"
        frontend_failure = "not-run"
        frontend_nonzero_report = "not-run"
        frontend_nonzero_no_report = "not-run"
        frontend_ok_continues = "not-run"
        e2e_missing_stats = "not-run"
        hardening_seed_backend_phase = "not-run"
        hardening_seed_e2e_phase = "not-run"
        hardening_seed_failure_backend = "not-run"
        hardening_seed_failure_e2e = "not-run"
        hardening_evidence_task10 = "not-run"
        backend_stage_isolation = "not-run"
        backend_tests_routing = "not-run"
        e2e_per_spec_fresh_stack = "not-run"
        e2e_fail_fast_not_run = "not-run"
        parent_aggregate_backend = "not-run"
        parent_aggregate_e2e = "not-run"
        preceding_phase_reason = "not-run"
        junit_all_skipped = "not-run"
        playwright_stats_complete = "not-run"
        e2e_success_exit_code = "not-run"
        image_cleanup_on_identity_failure = "not-run"
    }
    evidence = $contractEvidencePath
    error = $null
}

function Assert-Contract {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )

    if (-not $Condition) { throw $Message }
}

function Invoke-Entry {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$Script = $entryScript
    )

    $output = @(& pwsh -NoProfile -File $Script @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    return [ordered]@{
        exit_code = $exitCode
        text = (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
    }
}

function Set-ContractMode {
    param([Parameter(Mandatory)][string]$Mode)

    $env:HARDENING_CONTRACT_MODE = $Mode
    Remove-Item -LiteralPath $stackMarker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $dockerCallLog -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $npmCallLog -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $playwrightCallLog -Force -ErrorAction SilentlyContinue
}

function Read-DockerCalls {
    if (-not (Test-Path -LiteralPath $dockerCallLog)) { return @() }
    return @(
        Get-Content -LiteralPath $dockerCallLog |
            Where-Object { $_.Trim() -ne "" } |
            ForEach-Object { $_ | ConvertFrom-Json -AsHashtable }
    )
}

function Split-StackIntervals {
    <#
        任务10拓扑契约：把 docker 调用日志切成“up → down”库栈区间，
        并统计每个区间内显式 hardening seed 的次数。
        legacy 基线库栈 seed_count 必须为 0；hardening 库栈必须恰好为 1。
    #>
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Calls)

    $intervals = [System.Collections.Generic.List[object]]::new()
    $current = $null
    for ($index = 0; $index -lt $Calls.Count; $index++) {
        $arguments = @($Calls[$index].arguments)
        if (($arguments -contains "up") -and ($arguments -contains "app-test")) {
            Assert-Contract ($null -eq $current) "契约异常：上一个库栈未 down 就再次 up。"
            $current = [ordered]@{ up_index = $index; down_index = -1; seed_count = 0 }
        }
        elseif (($arguments -contains "run") -and ($arguments -contains "python") -and ($arguments -contains "tests/seed_hardening_e2e.py")) {
            Assert-Contract ($null -ne $current) "契约异常：库栈区间外出现 hardening seed。"
            $current.seed_count++
        }
        elseif ($arguments -contains "down") {
            Assert-Contract ($null -ne $current) "契约异常：没有 up 就出现 down。"
            $current.down_index = $index
            $intervals.Add($current) | Out-Null
            $current = $null
        }
    }
    Assert-Contract ($null -eq $current) "契约异常：库栈未以 down 结束。"
    return $intervals
}

function ConvertFrom-OutputJson {
    param([Parameter(Mandatory)][string]$Text)

    $start = $Text.IndexOf('{')
    $end = $Text.LastIndexOf('}')
    if ($start -lt 0 -or $end -le $start) { return $null }
    $candidate = $Text.Substring($start, $end - $start + 1)
    try { return ($candidate | ConvertFrom-Json) } catch { return $null }
}

function Read-NpmCalls {
    if (-not (Test-Path -LiteralPath $npmCallLog)) { return @() }
    return @(
        Get-Content -LiteralPath $npmCallLog |
            Where-Object { $_.Trim() -ne "" }
    )
}

function Read-PlaywrightCalls {
    if (-not (Test-Path -LiteralPath $playwrightCallLog)) { return @() }
    return @(
        Get-Content -LiteralPath $playwrightCallLog |
            Where-Object { $_.Trim() -ne "" }
    )
}

try {
    if (-not (Test-Path -LiteralPath $entryScript)) {
        throw "缺少待验收入口：$entryScript"
    }
    New-Item -ItemType Directory -Force -Path $fakeDockerRoot | Out-Null

    $fakeDockerContent = @'
$record = [ordered]@{arguments = @($args)}
[System.IO.File]::AppendAllText(
    $env:HARDENING_CONTRACT_DOCKER_LOG,
    "$($record | ConvertTo-Json -Compress)$([Environment]::NewLine)",
    [System.Text.UTF8Encoding]::new($false)
)

$mode = $env:HARDENING_CONTRACT_MODE
$marker = $env:HARDENING_CONTRACT_STACK_MARKER
$joined = [string]::Join(" ", @($args))

if ($mode -eq "occupied-project" -and $joined.Contains("com.docker.compose.project=supplier-risk-hardening-test")) {
    "foreign-resource"
    $global:LASTEXITCODE = 0
    return
}
if (($args -contains "ps") -or ($joined.StartsWith("network ls")) -or ($joined.StartsWith("volume ls"))) {
    if (Test-Path -LiteralPath $marker) { "owned-resource" }
    $global:LASTEXITCODE = 0
    return
}
if ($joined.StartsWith("build ")) {
    $global:LASTEXITCODE = 0
    return
}
if ($joined.StartsWith("image inspect")) {
    if ($joined.Contains("hardening.source_sha256")) {
        if ($mode -eq "image-identity-mismatch") {
            "contract-mismatched-fingerprint"
        }
        else {
            $env:HARDENING_SOURCE_FINGERPRINT
        }
    }
    elseif ($joined.Contains("hardening.owner")) {
        $env:HARDENING_RUN_ID
    }
    else {
        "sha256:contract-current-image"
    }
    $global:LASTEXITCODE = 0
    return
}
if ($args -contains "up") {
    [System.IO.File]::WriteAllText($marker, "owned", [System.Text.UTF8Encoding]::new($false))
    $global:LASTEXITCODE = 0
    return
}
if ($args -contains "run") {
    # 任务10契约：test-runner 显式叠加 seed_hardening_e2e.py；失败即终止由入口脚本保证。
    if ((@($args) -contains "python") -and (@($args) -contains "tests/seed_hardening_e2e.py")) {
        if ($mode -eq "seed-failure") {
            [Console]::Error.WriteLine("contract-forced-seed-failure")
            $global:LASTEXITCODE = 42
            return
        }
        $global:LASTEXITCODE = 0
        return
    }
    # 任务10拓扑：legacy/hardening 各阶段与逐 spec E2E 使用独立 JUnit 文件名。
    $junitTarget = $env:HARDENING_EVIDENCE_DIR
    $junitArg = @($args) | Where-Object { $_ -like '--junitxml=*' } | Select-Object -First 1
    if ($null -ne $junitArg) {
        $junitTarget = Join-Path $env:HARDENING_EVIDENCE_DIR ([System.IO.Path]::GetFileName($junitArg.Substring(11)))
    }
    switch ($mode) {
        "pytest-failure" {
            [Console]::Error.WriteLine("contract-forced-pytest-failure")
            $global:LASTEXITCODE = 7
            return
        }
        "junit-failure" {
            $cases = '<testcase classname="contract" name="one"/><testcase classname="contract" name="two"><failure message="forced"/></testcase>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"2`" failures=`"1`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"2`" failures=`"1`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText($junitTarget, $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 0
            return
        }
        "pytest-failure-with-report" {
            $cases = '<testcase classname="contract" name="one"/><testcase classname="contract" name="two"/><testcase classname="contract" name="three"/><testcase classname="contract" name="four"/><testcase classname="contract" name="five"><error message="forced"/></testcase>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"5`" failures=`"1`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"5`" failures=`"1`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText($junitTarget, $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 3
            return
        }
        "pytest-failure-no-report" {
            [Console]::Error.WriteLine("contract-forced-pytest-failure-no-report")
            $global:LASTEXITCODE = 5
            return
        }
        "junit-all-skipped" {
            $cases = '<testcase classname="contract" name="one"><skipped/></testcase><testcase classname="contract" name="two"><skipped/></testcase><testcase classname="contract" name="three"><skipped/></testcase>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"3`" failures=`"0`" errors=`"0`" skipped=`"3`"><testsuite name=`"contract`" tests=`"3`" failures=`"0`" errors=`"0`" skipped=`"3`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText($junitTarget, $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 0
            return
        }
        "zero-tests" {
            $cases = ""
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"0`" failures=`"0`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"0`" failures=`"0`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText($junitTarget, $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 0
            return
        }
        default {
            $cases = '<testcase classname="contract" name="one"/><testcase classname="contract" name="two"/>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"2`" failures=`"0`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"2`" failures=`"0`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText($junitTarget, $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 0
            return
        }
    }
}
if ($args -contains "down") {
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    $global:LASTEXITCODE = 0
    return
}
if ($joined.StartsWith("image rm")) {
    $global:LASTEXITCODE = 0
    return
}
[Console]::Error.WriteLine("contract-unexpected-docker-call: $joined")
$global:LASTEXITCODE = 91
'@
    [System.IO.File]::WriteAllText(
        $fakeDockerScript,
        $fakeDockerContent,
        [System.Text.UTF8Encoding]::new($false)
    )

    New-Item -ItemType Directory -Force -Path $fakeNpmRoot | Out-Null
    $fakeNpmContent = @'
param([Parameter(ValueFromRemainingArguments=$true)]$Rest, [switch]$Help)
$mode = $env:HARDENING_CONTRACT_MODE
$log = $env:HARDENING_CONTRACT_NPM_LOG
$joined = (@($Rest) -join ' ')
if (-not [string]::IsNullOrWhiteSpace($log)) {
    [System.IO.File]::AppendAllText(
        $log,
        ("npm :: " + $joined + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
}
$outArg = (@($Rest) | Where-Object { $_ -like '--outputFile=*' } | Select-Object -First 1)
$junitPath = if ($null -ne $outArg) { $outArg.Substring(13) } else { "" }
function Write-FrontendJunit {
    param([int]$Tests, [int]$Failures, [string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $cases = ""
    if ($Tests -gt 0) {
        $parts = @()
        for ($i = 1; $i -le $Tests; $i++) {
            if ($i -le $Failures) {
                $parts += ('<testcase classname="contract" name="c{0}"><failure message="forced"/></testcase>' -f $i)
            }
            else {
                $parts += ('<testcase classname="contract" name="c{0}"/>' -f $i)
            }
        }
        $cases = ($parts -join "")
    }
    $junit = '<?xml version="1.0"?><testsuites tests="{0}" failures="{1}" errors="0" skipped="0"><testsuite name="contract" tests="{0}" failures="{1}" errors="0" skipped="0">{2}</testsuite></testsuites>' -f $Tests, $Failures, $cases
    [System.IO.File]::WriteAllText($Path, $junit, [System.Text.UTF8Encoding]::new($false))
}
if ($joined -match 'test:unit') {
    switch ($mode) {
        "frontend-zero" { Write-FrontendJunit -Tests 0 -Failures 0 -Path $junitPath; exit 0 }
        "frontend-failure" { Write-FrontendJunit -Tests 2 -Failures 1 -Path $junitPath; exit 0 }
        "frontend-nonzero-report" { Write-FrontendJunit -Tests 3 -Failures 1 -Path $junitPath; exit 9 }
        "frontend-nonzero-no-report" { exit 8 }
        default { Write-FrontendJunit -Tests 3 -Failures 0 -Path $junitPath; exit 0 }
    }
}
exit 0
'@
    [System.IO.File]::WriteAllText($fakeNpmScript, $fakeNpmContent, [System.Text.UTF8Encoding]::new($false))

    New-Item -ItemType Directory -Force -Path $fakePlaywrightRoot | Out-Null
    $fakePlaywrightContent = @'
param([Parameter(ValueFromRemainingArguments=$true)]$Rest, [switch]$Help)
$mode = $env:HARDENING_CONTRACT_MODE
$log = $env:HARDENING_CONTRACT_PLAYWRIGHT_LOG
if (-not [string]::IsNullOrWhiteSpace($log)) {
    [System.IO.File]::AppendAllText(
        $log,
        ("playwright :: " + (@($Rest) -join ' ') + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
}
if ($mode -eq "e2e-missing-stats") {
    Write-Output '{"suites":[]}'
    exit 0
}
if ($mode -eq "e2e-stats-incomplete") {
    Write-Output '{"suites":[],"stats":{"expected":2,"unexpected":0,"flaky":0}}'
    exit 0
}
if ($mode -eq "e2e-stats-skipped") {
    Write-Output '{"suites":[],"stats":{"expected":2,"unexpected":0,"flaky":0,"skipped":1}}'
    exit 0
}
if ($mode -eq "e2e-stats-flaky") {
    Write-Output '{"suites":[],"stats":{"expected":2,"unexpected":0,"flaky":1,"skipped":0}}'
    exit 0
}
if ($mode -eq "e2e-spec-failure") {
    $failSpec = $env:HARDENING_CONTRACT_FAIL_SPEC
    $joined = (@($Rest) -join ' ')
    if (-not [string]::IsNullOrWhiteSpace($failSpec) -and $joined.Contains($failSpec)) {
        Write-Output '{"suites":[],"stats":{"expected":1,"unexpected":1,"flaky":0,"skipped":0}}'
        exit 1
    }
}
Write-Output '{"suites":[],"stats":{"expected":2,"unexpected":0,"flaky":0,"skipped":0}}'
exit 0
'@
    [System.IO.File]::WriteAllText($fakePlaywrightScript, $fakePlaywrightContent, [System.Text.UTF8Encoding]::new($false))

    $env:PATH = "$fakeNpmRoot;$fakeDockerRoot;$fakePlaywrightRoot;$originalPath"
    $env:HARDENING_CONTRACT_DOCKER_LOG = $dockerCallLog
    $env:HARDENING_CONTRACT_STACK_MARKER = $stackMarker
    $env:HARDENING_CONTRACT_NPM_LOG = $npmCallLog
    $env:HARDENING_CONTRACT_PLAYWRIGHT_LOG = $playwrightCallLog

    Set-ContractMode -Mode "normal"
    $unknownSuite = Invoke-Entry -Arguments @("-Suite", "frontend")
    Assert-Contract ($unknownSuite.exit_code -ne 0) "未知 Suite 未被拒绝。"
    Assert-Contract (@(Read-DockerCalls).Count -eq 0) "未知 Suite 在拒绝前调用了 Docker。"
    $result.checks.unknown_suite = "passed"

    Set-ContractMode -Mode "normal"
    $nonTestDatabase = Invoke-Entry -Arguments @(
        "-Suite", "backend", "-DatabaseUrl",
        "postgresql+psycopg://supplier_risk:test@postgres-test:5432/supplier_risk"
    )
    Assert-Contract ($nonTestDatabase.exit_code -ne 0) "非测试数据库未被拒绝。"
    Assert-Contract (@(Read-DockerCalls).Count -eq 0) "非测试数据库在拒绝前调用了 Docker。"
    $result.checks.non_test_database = "passed"

    Set-ContractMode -Mode "normal"
    $traversal = Invoke-Entry -Arguments @(
        "-Suite", "backend", "-Tests", "tests/../app/main.py"
    )
    Assert-Contract ($traversal.exit_code -ne 0) "路径遍历 Tests 未被拒绝。"
    Assert-Contract (@(Read-DockerCalls).Count -eq 0) "路径遍历在拒绝前调用了 Docker。"
    $result.checks.path_traversal = "passed"

    Set-ContractMode -Mode "normal"
    $unknownTest = Invoke-Entry -Arguments @(
        "-Suite", "backend", "-Tests", "tests/not_a_test.py"
    )
    Assert-Contract ($unknownTest.exit_code -ne 0) "不存在或非白名单测试未被拒绝。"
    Assert-Contract (@(Read-DockerCalls).Count -eq 0) "未知测试在拒绝前调用了 Docker。"
    $result.checks.unknown_test = "passed"

    Set-ContractMode -Mode "normal"
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        18080
    )
    $listenerStarted = $false
    try {
        try {
            $listener.Start()
            $listenerStarted = $true
        }
        catch [System.Net.Sockets.SocketException] {
            # 若真实环境已占用该端口，直接复用这一运行时事实验证拒绝契约；绝不停止占用者。
        }
        $occupiedPort = Invoke-Entry -Arguments @(
            "-Suite", "backend", "-Tests", "tests/test_health.py"
        )
    }
    finally {
        if ($listenerStarted) { $listener.Stop() }
    }
    Assert-Contract ($occupiedPort.exit_code -ne 0) "占用端口时入口未失败。"
    Assert-Contract (@(Read-DockerCalls).Count -eq 0) "占用端口时仍调用了 Docker。"
    $result.checks.occupied_port = "passed"

    $probeListener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $probeListener.Start()
    $probePort = ([System.Net.IPEndPoint]$probeListener.LocalEndpoint).Port
    $probeListener.Stop()
    $entryContent = [System.IO.File]::ReadAllText($entryScript, [System.Text.UTF8Encoding]::new($false))
    $probeContent = $entryContent.Replace("18080", $probePort.ToString())
    [System.IO.File]::WriteAllText($probeEntryScript, $probeContent, [System.Text.UTF8Encoding]::new($false))

    $frontendProbeContent = $entryContent.Replace("18080", $probePort.ToString())
    $frontendProbeContent = $frontendProbeContent.Replace('if ($Suite -in @("e2e", "all")) {', 'if ($false) {')
    [System.IO.File]::WriteAllText($frontendProbeEntryScript, $frontendProbeContent, [System.Text.UTF8Encoding]::new($false))

    $e2eProbeContent = $entryContent.Replace("18080", $probePort.ToString())
    $e2eProbeContent = $e2eProbeContent.Replace('$health = Invoke-RestMethod -Uri "$baseUrl/api/v1/system/health"', '$health = [pscustomobject]@{ status = "ok"; database = "ok" }')
    $e2eProbeContent = $e2eProbeContent.Replace('$playwrightExecutable = Join-Path $frontendRoot "node_modules/.bin/playwright.cmd"', ('$playwrightExecutable = "' + $fakePlaywrightScript + '"'))
    [System.IO.File]::WriteAllText($e2eProbeEntryScript, $e2eProbeContent, [System.Text.UTF8Encoding]::new($false))

    Set-ContractMode -Mode "occupied-project"
    $occupiedProject = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($occupiedProject.exit_code -ne 0) "占用 Compose 项目时入口未失败。"
    $occupiedCalls = Read-DockerCalls
    Assert-Contract (
        @($occupiedCalls | Where-Object { $_.arguments -contains "build" }).Count -eq 0
    ) "占用 Compose 项目时仍构建了镜像。"
    Assert-Contract (
        @($occupiedCalls | Where-Object { $_.arguments -contains "down" }).Count -eq 0
    ) "占用 Compose 项目时错误清理了他人资源。"
    Assert-Contract ($occupiedCalls.Count -gt 0) "占用 Compose 项目用例未执行 Docker 资源查询。"
    $result.checks.occupied_project = "passed"

    Set-ContractMode -Mode "pytest-failure"
    $pytestFailure = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($pytestFailure.exit_code -eq 7) "pytest 退出码 7 未被原样传播。"
    $pytestFailureObj = ConvertFrom-OutputJson -Text $pytestFailure.text
    Assert-Contract ($null -ne $pytestFailureObj) "pytest-failure 场景未输出机器可读摘要。"
    Assert-Contract ($pytestFailureObj.phases.backend.stages.legacy.status -eq "failed") "pytest 失败未在 legacy 阶段标记 failed。"
    Assert-Contract ($pytestFailureObj.phases.backend.stages.hardening.status -eq "not-run") "legacy 失败后 hardening 阶段未记录 not-run。"
    $result.checks.pytest_exit_code = "passed"

    Set-ContractMode -Mode "zero-tests"
    $zeroTests = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($zeroTests.exit_code -ne 0) "JUnit 零用例被伪装为成功。"
    $zeroTestsObj = ConvertFrom-OutputJson -Text $zeroTests.text
    Assert-Contract ($null -ne $zeroTestsObj) "zero-tests 场景未输出机器可读摘要。"
    Assert-Contract ($zeroTestsObj.phases.backend.stages.legacy.status -eq "failed") "JUnit 零用例未在 legacy 阶段标记 failed。"
    $result.checks.zero_tests = "passed"

    Set-ContractMode -Mode "normal"
    $success = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($success.exit_code -eq 0) "非零 JUnit 合规场景未成功。"
    Assert-Contract ($success.text.Contains('"tests": 2')) "摘要未记录实际非零用例数。"
    $successCalls = Read-DockerCalls
    Assert-Contract (
        @($successCalls | Where-Object { @($_.arguments) -contains "down" }).Count -eq 1
    ) "本轮拥有的库栈未且仅未清理一次。"
    $result.checks.successful_nonzero_junit = "passed"
    $result.checks.cleanup_ownership = "passed"

    Set-ContractMode -Mode "junit-failure"
    $junitFailure = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($junitFailure.exit_code -ne 0) "JUnit testcase 含 failure/error 但入口判为通过。"
    $junitFailureObj = ConvertFrom-OutputJson -Text $junitFailure.text
    Assert-Contract ($null -ne $junitFailureObj) "junit-failure 场景未输出机器可读摘要。"
    Assert-Contract ($junitFailureObj.phases.backend.status -eq "failed") "JUnit 含失败时阶段未标记 failed。"
    Assert-Contract ($junitFailureObj.phases.backend.stages.legacy.status -eq "failed") "JUnit 含失败时 legacy 阶段未标记 failed。"
    $result.checks.exit_zero_with_failures = "passed"

    Set-ContractMode -Mode "pytest-failure-with-report"
    $nonzeroWithReport = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($nonzeroWithReport.exit_code -eq 3) "非零 pytest 退出码 3 未被传播。"
    $nwrObj = ConvertFrom-OutputJson -Text $nonzeroWithReport.text
    Assert-Contract ($null -ne $nwrObj) "pytest-with-report 场景未输出机器可读摘要。"
    Assert-Contract ($nwrObj.phases.backend.status -eq "failed") "非零 pytest 且有报告时阶段未标记 failed。"
    Assert-Contract ($nwrObj.phases.backend.stages.legacy.status -eq "failed") "非零 pytest 且有报告时 legacy 阶段未标记 failed。"
    Assert-Contract ($nwrObj.phases.backend.stages.legacy.exit_code -eq 3) "失败阶段未记录原始退出码 3。"
    Assert-Contract ($nwrObj.phases.backend.stages.legacy.tests -eq 5) "失败阶段未保存 JUnit 实际用例数。"
    Assert-Contract ($nwrObj.phases.backend.stages.legacy.failures -eq 1) "失败阶段未保存 JUnit 失败数。"
    Assert-Contract ($nwrObj.phases.backend.stages.legacy.junit_available -eq $true) "有报告却标记报告不可获得。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$nwrObj.phases.backend.stages.legacy.started_at_utc)) "失败阶段缺少开始时间。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$nwrObj.phases.backend.stages.legacy.finished_at_utc)) "失败阶段缺少结束时间。"
    $result.checks.nonzero_with_report = "passed"

    Set-ContractMode -Mode "pytest-failure-no-report"
    $nonzeroNoReport = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($nonzeroNoReport.exit_code -eq 5) "非零 pytest 退出码 5 未被传播。"
    $nnrObj = ConvertFrom-OutputJson -Text $nonzeroNoReport.text
    Assert-Contract ($null -ne $nnrObj) "pytest-no-report 场景未输出机器可读摘要。"
    Assert-Contract ($nnrObj.phases.backend.status -eq "failed") "缺报告时阶段未标记 failed。"
    Assert-Contract ($nnrObj.phases.backend.stages.legacy.status -eq "failed") "缺报告时 legacy 阶段未标记 failed。"
    Assert-Contract ($nnrObj.phases.backend.stages.legacy.exit_code -eq 5) "缺报告阶段未记录原始退出码 5。"
    Assert-Contract ($nnrObj.phases.backend.stages.legacy.junit_available -eq $false) "缺报告未明确记录报告不可获得。"
    Assert-Contract ($nnrObj.phases.backend.stages.legacy.junit_diagnostic -eq "missing") "缺报告未标记 missing 诊断。"
    $result.checks.nonzero_without_report = "passed"

    Set-ContractMode -Mode "frontend-zero"
    $frontZero = Invoke-Entry -Script $frontendProbeEntryScript -Arguments @("-Suite", "all")
    Assert-Contract ($frontZero.exit_code -ne 0) "前端零用例被伪装为通过。"
    $frontZeroObj = ConvertFrom-OutputJson -Text $frontZero.text
    Assert-Contract ($null -ne $frontZeroObj) "前端零用例场景未输出机器可读摘要。"
    Assert-Contract ($frontZeroObj.phases.frontend.status -eq "failed") "前端零用例阶段未标记 failed。"
    $frontZeroNpmCalls = Read-NpmCalls
    Assert-Contract (@($frontZeroNpmCalls | Where-Object { $_ -match 'typecheck|build' }).Count -eq 0) "前端零用例仍继续了 typecheck/build。"
    $result.checks.frontend_zero = "passed"

    Set-ContractMode -Mode "frontend-failure"
    $frontFailure = Invoke-Entry -Script $frontendProbeEntryScript -Arguments @("-Suite", "all")
    Assert-Contract ($frontFailure.exit_code -ne 0) "前端 JUnit 含 failure/error 但入口判为通过。"
    $frontFailureObj = ConvertFrom-OutputJson -Text $frontFailure.text
    Assert-Contract ($null -ne $frontFailureObj) "前端含失败场景未输出机器可读摘要。"
    Assert-Contract ($frontFailureObj.phases.frontend.status -eq "failed") "前端含失败阶段未标记 failed。"
    $frontFailureNpmCalls = Read-NpmCalls
    Assert-Contract (@($frontFailureNpmCalls | Where-Object { $_ -match 'typecheck|build' }).Count -eq 0) "前端含失败仍继续了 typecheck/build。"
    $result.checks.frontend_failure = "passed"

    Set-ContractMode -Mode "frontend-nonzero-report"
    $frontNonzeroReport = Invoke-Entry -Script $frontendProbeEntryScript -Arguments @("-Suite", "all")
    Assert-Contract ($frontNonzeroReport.exit_code -eq 9) "前端非零退出码 9 未被传播。"
    $fNR = ConvertFrom-OutputJson -Text $frontNonzeroReport.text
    Assert-Contract ($null -ne $fNR) "前端非零带报告场景未输出机器可读摘要。"
    Assert-Contract ($fNR.phases.frontend.status -eq "failed") "前端非零带报告阶段未标记 failed。"
    Assert-Contract ($fNR.phases.frontend.exit_code -eq 9) "前端失败阶段未记录原始退出码 9。"
    Assert-Contract ($fNR.phases.frontend.tests -eq 3) "前端失败阶段未保存 JUnit 实际用例数。"
    Assert-Contract ($fNR.phases.frontend.failures -eq 1) "前端失败阶段未保存 JUnit 失败数。"
    Assert-Contract ($fNR.phases.frontend.junit_available -eq $true) "前端有报告却标记不可获得。"
    $frontNonzeroNpmCalls = Read-NpmCalls
    Assert-Contract (@($frontNonzeroNpmCalls | Where-Object { $_ -match 'typecheck|build' }).Count -eq 0) "前端非零带报告仍继续 typecheck/build。"
    $result.checks.frontend_nonzero_report = "passed"

    Set-ContractMode -Mode "frontend-nonzero-no-report"
    $frontNonzeroNoReport = Invoke-Entry -Script $frontendProbeEntryScript -Arguments @("-Suite", "all")
    Assert-Contract ($frontNonzeroNoReport.exit_code -eq 8) "前端缺报告非零退出码 8 未被传播。"
    $fNNR = ConvertFrom-OutputJson -Text $frontNonzeroNoReport.text
    Assert-Contract ($null -ne $fNNR) "前端缺报告场景未输出机器可读摘要。"
    Assert-Contract ($fNNR.phases.frontend.status -eq "failed") "前端缺报告阶段未标记 failed。"
    Assert-Contract ($fNNR.phases.frontend.exit_code -eq 8) "前端缺报告阶段未记录原始退出码 8。"
    Assert-Contract ($fNNR.phases.frontend.junit_available -eq $false) "前端缺报告未明确记录不可获得。"
    Assert-Contract ($fNNR.phases.frontend.junit_diagnostic -eq "missing") "前端缺报告未标记 missing 诊断。"
    $result.checks.frontend_nonzero_no_report = "passed"

    Set-ContractMode -Mode "frontend-ok"
    $frontOk = Invoke-Entry -Script $frontendProbeEntryScript -Arguments @("-Suite", "all")
    $fOk = ConvertFrom-OutputJson -Text $frontOk.text
    Assert-Contract ($null -ne $fOk) "前端正常场景未输出机器可读摘要。"
    Assert-Contract ($fOk.phases.frontend.status -eq "passed") "前端正常报告未标记 passed。"
    Assert-Contract ($fOk.phases.frontend.typecheck -eq "passed") "前端正常未继续 typecheck。"
    Assert-Contract ($fOk.phases.frontend.build -eq "passed") "前端正常未继续 build。"
    $frontOkNpmCalls = Read-NpmCalls
    Assert-Contract (@($frontOkNpmCalls | Where-Object { $_ -match 'typecheck' }).Count -ge 1) "前端正常未调用 typecheck。"
    Assert-Contract (@($frontOkNpmCalls | Where-Object { $_ -match 'build' }).Count -ge 1) "前端正常未调用 build。"
    $result.checks.frontend_ok_continues = "passed"

    Set-ContractMode -Mode "e2e-missing-stats"
    $e2eMissingStats = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    $e2eMissingObj = ConvertFrom-OutputJson -Text $e2eMissingStats.text
    Assert-Contract ($null -ne $e2eMissingObj) "e2e-missing-stats 场景未输出机器可读摘要。"
    Assert-Contract ($e2eMissingObj.phases.e2e.status -eq "failed") "Playwright 缺 stats 未标记阶段 failed。"
    $e2eMissingHardeningStage = $e2eMissingObj.phases.e2e.stages."current-version-hardening"
    Assert-Contract ($null -ne $e2eMissingHardeningStage) "e2e 摘要缺少 hardening spec stage 记录。"
    Assert-Contract ($e2eMissingHardeningStage.status -eq "failed") "Playwright 缺 stats 未在首个 spec 阶段标记 failed。"
    Assert-Contract ($e2eMissingHardeningStage.report_available -eq $true) "Playwright 可解析报告被误判不可获得。"
    Assert-Contract ($e2eMissingHardeningStage.report_diagnostic -eq "invalid") "Playwright 缺 stats 未记录报告无效。"
    Assert-Contract (@(Read-PlaywrightCalls).Count -eq 1) "报告无效后未 fail-fast 停止后续 spec。"
    $result.checks.e2e_missing_stats = "passed"

    # ------------------------------------------------------------------ #
    # 任务10拓扑：legacy/hardening 分库分阶段、逐 spec E2E 重建、fail-fast
    # ------------------------------------------------------------------ #
    $specNameList = [System.Collections.Generic.List[string]]::new()
    foreach ($item in @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "frontend/tests/e2e") -Filter "*.spec.ts" -File)) {
        $specNameList.Add($item.Name)
    }
    $specNameList.Sort([System.StringComparer]::Ordinal)
    $expectedE2eSpecNames = @($specNameList)
    $expectedE2eCount = $expectedE2eSpecNames.Count
    Assert-Contract ($expectedE2eCount -ge 2) "契约前置：frontend/tests/e2e 下应发现多个 spec。"
    $hardeningSpecIndex = [array]::IndexOf($expectedE2eSpecNames, "current-version-hardening.spec.ts")
    Assert-Contract ($hardeningSpecIndex -ge 0) "契约前置：未发现 current-version-hardening.spec.ts。"

    # backend 全量：legacy 基线库（仅隐式 seed_e2e）+ hardening 库（恰好一次 hardening seed）。
    Set-ContractMode -Mode "normal"
    $backendTopology = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "backend")
    Assert-Contract ($backendTopology.exit_code -eq 0) "新拓扑 backend 全量场景未成功。"
    $topologyCalls = @(Read-DockerCalls)
    $topologyIntervals = @(Split-StackIntervals -Calls $topologyCalls)
    Assert-Contract ($topologyIntervals.Count -eq 2) "backend 全量应重建 2 个独立库栈（legacy/hardening），实际 $($topologyIntervals.Count)。"
    Assert-Contract ($topologyIntervals[0].seed_count -eq 0) "legacy 基线库栈不得执行 hardening seed。"
    Assert-Contract ($topologyIntervals[1].seed_count -eq 1) "hardening 库栈未恰好执行一次 hardening seed。"
    $topologyPytestIndexes = @(
        for ($index = 0; $index -lt $topologyCalls.Count; $index++) {
            if (@($topologyCalls[$index].arguments) -contains "pytest") { $index }
        }
    )
    Assert-Contract ($topologyPytestIndexes.Count -eq 2) "backend 全量应恰好两次 pytest，实际 $($topologyPytestIndexes.Count)。"
    Assert-Contract ($topologyIntervals[0].up_index -lt $topologyPytestIndexes[0]) "legacy pytest 未在 legacy 库栈启动后执行。"
    Assert-Contract ($topologyIntervals[1].up_index -lt $topologyPytestIndexes[1]) "hardening pytest 未在 hardening 库栈启动后执行。"
    $topologyLegacyArgs = @($topologyCalls[$topologyPytestIndexes[0]].arguments)
    Assert-Contract ($topologyLegacyArgs -contains "--ignore=tests/test_hardening_integration.py") "legacy 全量 pytest 未排除 test_hardening_integration.py。"
    Assert-Contract (-not ($topologyLegacyArgs -contains "tests/test_hardening_integration.py")) "legacy 基线库直接运行了 hardening 集成测试。"
    $topologyHardeningArgs = @($topologyCalls[$topologyPytestIndexes[1]].arguments)
    $topologyHardeningSelected = @($topologyHardeningArgs | Where-Object { $_ -like 'tests/*' -and $_ -notlike '--*' })
    Assert-Contract (
        ($topologyHardeningSelected.Count -eq 1) -and ($topologyHardeningSelected[0] -eq "tests/test_hardening_integration.py")
    ) "hardening 阶段未仅运行 tests/test_hardening_integration.py。"
    $topologySummary = ConvertFrom-OutputJson -Text $backendTopology.text
    Assert-Contract ($null -ne $topologySummary) "新拓扑 backend 场景未输出机器可读摘要。"
    Assert-Contract ($topologySummary.phases.backend.stages.legacy.status -eq "passed") "backend 摘要未记录 legacy 基线库阶段通过。"
    Assert-Contract ($topologySummary.phases.backend.stages.hardening.status -eq "passed") "backend 摘要未记录 hardening 库阶段通过。"
    $result.checks.backend_stage_isolation = "passed"

    $topologySeedIndexes = @(
        for ($index = 0; $index -lt $topologyCalls.Count; $index++) {
            $arguments = @($topologyCalls[$index].arguments)
            if (($arguments -contains "run") -and ($arguments -contains "python") -and ($arguments -contains "tests/seed_hardening_e2e.py")) { $index }
        }
    )
    Assert-Contract ($topologySeedIndexes.Count -eq 1) "backend 全量未恰好显式执行一次 seed_hardening_e2e.py。"
    Assert-Contract ($topologyIntervals[1].up_index -lt $topologySeedIndexes[0]) "hardening seed 未在其隔离库栈启动后执行。"
    Assert-Contract ($topologySeedIndexes[0] -lt $topologyPytestIndexes[1]) "hardening seed 未在 hardening pytest 之前执行。"
    Assert-Contract ($topologySummary.phases.backend.stages.hardening.seed.status -eq "passed") "backend 摘要未记录 hardening seed 通过。"
    Assert-Contract ($topologySummary.phases.backend.stages.hardening.seed.exit_code -eq 0) "backend 摘要未记录 hardening seed 退出码。"
    $result.checks.hardening_seed_backend_phase = "passed"

    # -Tests 定向按类别分流：legacy 路径只建基线库，hardening 路径只建 hardening 库。
    Set-ContractMode -Mode "normal"
    $routingLegacyOnly = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($routingLegacyOnly.exit_code -eq 0) "定向 legacy 测试场景未成功。"
    $routingLegacyIntervals = @(Split-StackIntervals -Calls @(Read-DockerCalls))
    Assert-Contract ($routingLegacyIntervals.Count -eq 1) "定向 legacy 测试应仅重建 1 个基线库栈，实际 $($routingLegacyIntervals.Count)。"
    Assert-Contract ($routingLegacyIntervals[0].seed_count -eq 0) "定向 legacy 栈不得执行 hardening seed。"
    $routingLegacySummary = ConvertFrom-OutputJson -Text $routingLegacyOnly.text
    Assert-Contract ($routingLegacySummary.phases.backend.stages.legacy.status -eq "passed") "定向 legacy 摘要未记录 legacy 阶段通过。"
    Assert-Contract ($routingLegacySummary.phases.backend.stages.hardening.status -eq "not-run") "未选中的 hardening 阶段未记录 not-run。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$routingLegacySummary.phases.backend.stages.hardening.reason)) "not-run 的 hardening 阶段缺少 reason。"

    Set-ContractMode -Mode "normal"
    $routingHardeningOnly = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_hardening_integration.py"
    )
    Assert-Contract ($routingHardeningOnly.exit_code -eq 0) "定向 hardening 测试场景未成功。"
    $routingHardeningIntervals = @(Split-StackIntervals -Calls @(Read-DockerCalls))
    Assert-Contract ($routingHardeningIntervals.Count -eq 1) "定向 hardening 测试应仅重建 1 个 hardening 库栈，实际 $($routingHardeningIntervals.Count)。"
    Assert-Contract ($routingHardeningIntervals[0].seed_count -eq 1) "定向 hardening 栈未恰好执行一次 hardening seed。"
    $routingHardeningSummary = ConvertFrom-OutputJson -Text $routingHardeningOnly.text
    Assert-Contract ($routingHardeningSummary.phases.backend.stages.legacy.status -eq "not-run") "未选中的 legacy 阶段未记录 not-run。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$routingHardeningSummary.phases.backend.stages.legacy.reason)) "not-run 的 legacy 阶段缺少 reason。"
    Assert-Contract ($routingHardeningSummary.phases.backend.stages.hardening.status -eq "passed") "定向 hardening 摘要未记录 hardening 阶段通过。"
    $result.checks.backend_tests_routing = "passed"

    # e2e 逐 spec：每个 spec 独立库栈，字典序运行；hardening spec 栈恰好一次 seed。
    Set-ContractMode -Mode "normal"
    $e2eTopology = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    Assert-Contract ($e2eTopology.exit_code -eq 0) "逐 spec e2e 全量场景未成功。"
    $e2eTopologyCalls = @(Read-DockerCalls)
    $e2eIntervals = @(Split-StackIntervals -Calls $e2eTopologyCalls)
    $e2ePlaywrightCalls = @(Read-PlaywrightCalls)
    Assert-Contract ($e2eIntervals.Count -eq $expectedE2eCount) "每个 spec 应重建独立库栈：期望 $expectedE2eCount，实际 $($e2eIntervals.Count)。"
    Assert-Contract ($e2ePlaywrightCalls.Count -eq $expectedE2eCount) "每个 spec 应恰好一次 Playwright 调用：期望 $expectedE2eCount，实际 $($e2ePlaywrightCalls.Count)。"
    for ($index = 0; $index -lt $expectedE2eCount; $index++) {
        $expectedSpec = "tests/e2e/$($expectedE2eSpecNames[$index])"
        Assert-Contract ($e2ePlaywrightCalls[$index].Contains($expectedSpec)) "Playwright 未按稳定字典序运行第 $($index + 1) 个 spec（期望 $expectedSpec）。"
    }
    for ($index = 0; $index -lt $e2eIntervals.Count; $index++) {
        $expectedSeeds = if ($index -eq $hardeningSpecIndex) { 1 } else { 0 }
        Assert-Contract ($e2eIntervals[$index].seed_count -eq $expectedSeeds) "第 $($index + 1) 个 e2e 库栈的 hardening seed 次数应为 $expectedSeeds，实际 $($e2eIntervals[$index].seed_count)。"
    }
    $e2eTopologySummary = ConvertFrom-OutputJson -Text $e2eTopology.text
    Assert-Contract ($null -ne $e2eTopologySummary) "逐 spec e2e 场景未输出机器可读摘要。"
    $e2eStageRecords = $e2eTopologySummary.phases.e2e.stages
    Assert-Contract ($null -ne $e2eStageRecords) "e2e 摘要缺少 stages 记录。"
    foreach ($specName in $expectedE2eSpecNames) {
        $stem = $specName -replace '\.spec\.ts$', ''
        $stageProperty = $e2eStageRecords.psobject.Properties[$stem]
        Assert-Contract ($null -ne $stageProperty) "e2e 摘要缺少 spec stage 记录：$stem"
        Assert-Contract ($stageProperty.Value.status -eq "passed") "e2e stage $stem 未标记 passed。"
    }
    $result.checks.e2e_per_spec_fresh_stack = "passed"

    $e2eSeedIndexes = @(
        for ($index = 0; $index -lt $e2eTopologyCalls.Count; $index++) {
            $arguments = @($e2eTopologyCalls[$index].arguments)
            if (($arguments -contains "run") -and ($arguments -contains "python") -and ($arguments -contains "tests/seed_hardening_e2e.py")) { $index }
        }
    )
    Assert-Contract ($e2eSeedIndexes.Count -eq 1) "e2e 阶段未恰好显式执行一次 seed_hardening_e2e.py。"
    Assert-Contract ($e2eIntervals[$hardeningSpecIndex].up_index -lt $e2eSeedIndexes[0]) "e2e 阶段 seed 未在其隔离库栈启动后执行。"
    Assert-Contract ($e2eSeedIndexes[0] -lt $e2eIntervals[$hardeningSpecIndex].down_index) "e2e 阶段 seed 未在其库栈关闭前执行。"
    $e2eHardeningStage = $e2eStageRecords.psobject.Properties["current-version-hardening"].Value
    Assert-Contract ($e2eHardeningStage.seed.status -eq "passed") "hardening spec 阶段未记录 seed 恰好一次通过。"
    $legacySpecIndex = if ($hardeningSpecIndex -eq 0) { 1 } else { 0 }
    $legacyStem = $expectedE2eSpecNames[$legacySpecIndex] -replace '\.spec\.ts$', ''
    $e2eLegacyStage = $e2eStageRecords.psobject.Properties[$legacyStem].Value
    Assert-Contract ($e2eLegacyStage.seed.status -eq "not-run") "legacy spec 阶段不得执行 hardening seed。"
    $result.checks.hardening_seed_e2e_phase = "passed"

    # e2e fail-fast：首个失败 spec 后停止调用 Playwright，不再启动新栈，其余 spec 记 not-run。
    $env:HARDENING_CONTRACT_FAIL_SPEC = "isolated-stack.spec.ts"
    Set-ContractMode -Mode "e2e-spec-failure"
    $e2eFailFast = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    $env:HARDENING_CONTRACT_FAIL_SPEC = $null
    Assert-Contract ($e2eFailFast.exit_code -ne 0) "spec 失败未导致整体失败。"
    $failFastPlaywrightCalls = @(Read-PlaywrightCalls)
    Assert-Contract ($failFastPlaywrightCalls.Count -eq 2) "首个失败 spec 后应停止调用 Playwright：期望 2 次，实际 $($failFastPlaywrightCalls.Count)。"
    $failFastIntervals = @(Split-StackIntervals -Calls @(Read-DockerCalls))
    Assert-Contract ($failFastIntervals.Count -eq 2) "首个失败 spec 后不得再启动新库栈：期望 2 个，实际 $($failFastIntervals.Count)。"
    $failFastSummary = ConvertFrom-OutputJson -Text $e2eFailFast.text
    Assert-Contract ($null -ne $failFastSummary) "e2e fail-fast 场景未输出机器可读摘要。"
    $failFastStages = $failFastSummary.phases.e2e.stages
    Assert-Contract ($failFastStages."current-version-hardening".status -eq "passed") "失败 spec 之前的 stage 应为 passed。"
    Assert-Contract ($failFastStages."isolated-stack".status -eq "failed") "失败 spec 未标记 failed。"
    $failFastNotRunCount = 0
    foreach ($property in $failFastStages.psobject.Properties) {
        if ($property.Value.status -eq "not-run") {
            $failFastNotRunCount++
            Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$property.Value.reason)) "not-run stage $($property.Name) 缺少 reason。"
        }
    }
    Assert-Contract ($failFastNotRunCount -eq ($expectedE2eCount - 2)) "失败后未运行的 spec 应记录 not-run：期望 $($expectedE2eCount - 2)，实际 $failFastNotRunCount。"
    Assert-Contract ($failFastSummary.phases.e2e.status -eq "failed") "e2e 阶段失败未汇总为 failed。"
    $result.checks.e2e_fail_fast_not_run = "passed"

    # backend：hardening seed 失败时 legacy 基线库结果保留，hardening pytest 不得执行。
    Set-ContractMode -Mode "seed-failure"
    $seedFailure = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "backend")
    Assert-Contract ($seedFailure.exit_code -eq 42) "seed 失败退出码 42 未被原样传播。"
    $seedFailureCalls = @(Read-DockerCalls)
    $seedFailureIntervals = @(Split-StackIntervals -Calls $seedFailureCalls)
    Assert-Contract ($seedFailureIntervals.Count -eq 2) "seed 失败场景应已启动 legacy 与 hardening 两个库栈。"
    Assert-Contract ($seedFailureIntervals[1].seed_count -eq 1) "hardening 库栈未执行 seed。"
    $seedFailurePytestCount = @($seedFailureCalls | Where-Object { @($_.arguments) -contains "pytest" }).Count
    Assert-Contract ($seedFailurePytestCount -eq 1) "hardening seed 失败后不得再执行 hardening pytest（全量仅允许 legacy 一次），实际 $seedFailurePytestCount 次。"
    $seedFailureObj = ConvertFrom-OutputJson -Text $seedFailure.text
    Assert-Contract ($null -ne $seedFailureObj) "seed 失败场景未输出机器可读摘要。"
    Assert-Contract ($seedFailureObj.phases.backend.stages.legacy.status -eq "passed") "seed 失败不应影响已通过的 legacy 基线库阶段。"
    Assert-Contract ($seedFailureObj.phases.backend.stages.hardening.seed.status -eq "failed") "seed 失败未在摘要中标记 failed。"
    Assert-Contract ($seedFailureObj.phases.backend.stages.hardening.status -eq "failed") "hardening 阶段未因 seed 失败标记 failed。"
    Assert-Contract ($seedFailureObj.status -eq "failed") "seed 失败场景整体状态未标记 failed。"
    $result.checks.hardening_seed_failure_backend = "passed"

    Set-ContractMode -Mode "seed-failure"
    $seedFailureE2e = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    Assert-Contract ($seedFailureE2e.exit_code -eq 42) "e2e 阶段 seed 失败退出码未被原样传播。"
    Assert-Contract (@(Read-PlaywrightCalls).Count -eq 0) "e2e 阶段 seed 失败后仍调用了 Playwright。"
    $seedFailureE2eIntervals = @(Split-StackIntervals -Calls @(Read-DockerCalls))
    Assert-Contract ($seedFailureE2eIntervals.Count -eq 1) "e2e seed 失败后不得为后续 spec 启动新库栈。"
    $seedFailureE2eObj = ConvertFrom-OutputJson -Text $seedFailureE2e.text
    Assert-Contract ($null -ne $seedFailureE2eObj) "e2e seed 失败场景未输出机器可读摘要。"
    $e2eSeedFailureStages = $seedFailureE2eObj.phases.e2e.stages
    Assert-Contract ($e2eSeedFailureStages."current-version-hardening".seed.status -eq "failed") "e2e seed 失败未在摘要中标记 failed。"
    Assert-Contract ($e2eSeedFailureStages."current-version-hardening".status -eq "failed") "e2e hardening spec 阶段未标记 failed。"
    $e2eSeedFailureNotRunCount = 0
    foreach ($property in $e2eSeedFailureStages.psobject.Properties) {
        if ($property.Value.status -eq "not-run") { $e2eSeedFailureNotRunCount++ }
    }
    Assert-Contract ($e2eSeedFailureNotRunCount -eq ($expectedE2eCount - 1)) "e2e seed 失败后其余 spec 应记录 not-run。"
    $result.checks.hardening_seed_failure_e2e = "passed"

    Assert-Contract ($topologySummary.evidence_directory -like "*task-10-current-version-hardening*") "入口证据目录未指向任务10专项目录。"
    Assert-Contract ($topologySummary.evidence_directory -notlike "*task-1-current-version-hardening*") "入口证据目录仍在任务1目录。"
    $result.checks.hardening_evidence_task10 = "passed"

    # 父级 phases.backend 聚合：仅汇总已运行且报告可解析的 stage，not-run 不伪造数值。
    Set-ContractMode -Mode "normal"
    $backendParent = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "backend")
    Assert-Contract ($backendParent.exit_code -eq 0) "父级聚合场景 backend 全量未成功。"
    $backendParentObj = ConvertFrom-OutputJson -Text $backendParent.text
    Assert-Contract ($null -ne $backendParentObj) "父级聚合场景未输出机器可读摘要。"
    Assert-Contract ($null -ne $backendParentObj.phases.backend.tests) "backend 父级缺少 tests 聚合。"
    Assert-Contract ($null -ne $backendParentObj.phases.backend.failures) "backend 父级缺少 failures 聚合。"
    Assert-Contract ($null -ne $backendParentObj.phases.backend.skipped) "backend 父级缺少 skipped 聚合。"
    Assert-Contract ($backendParentObj.phases.backend.tests -eq 4) "backend 父级 tests 未汇总两个已运行 stage（期望 4，实际 $($backendParentObj.phases.backend.tests)）。"
    Assert-Contract ($backendParentObj.phases.backend.failures -eq 0) "backend 父级 failures 应为 0。"
    Assert-Contract ($backendParentObj.phases.backend.skipped -eq 0) "backend 父级 skipped 应为 0。"
    Assert-Contract ($backendParentObj.phases.backend.status -eq "passed") "backend 父级未标记 passed。"

    Set-ContractMode -Mode "pytest-failure-with-report"
    $backendParentFail = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "backend", "-Tests", "tests/test_health.py")
    $backendParentFailObj = ConvertFrom-OutputJson -Text $backendParentFail.text
    Assert-Contract ($null -ne $backendParentFailObj) "backend 父级失败场景未输出摘要。"
    Assert-Contract ($backendParentFailObj.phases.backend.tests -eq 5) "backend 父级失败时未保留已解析 stage 的 tests（期望 5）。"
    Assert-Contract ($backendParentFailObj.phases.backend.failures -eq 1) "backend 父级失败时未聚合 failures（期望 1）。"
    Assert-Contract ($backendParentFailObj.phases.backend.status -eq "failed") "backend 父级失败时未标记 failed。"
    $result.checks.parent_aggregate_backend = "passed"

    # 父级 phases.e2e 聚合：仅汇总已运行 spec 的 Playwright 计数。
    Set-ContractMode -Mode "normal"
    $e2eParent = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    Assert-Contract ($e2eParent.exit_code -eq 0) "父级聚合场景 e2e 全量未成功。"
    $e2eParentObj = ConvertFrom-OutputJson -Text $e2eParent.text
    Assert-Contract ($null -ne $e2eParentObj) "e2e 父级聚合场景未输出摘要。"
    Assert-Contract ($null -ne $e2eParentObj.phases.e2e.tests) "e2e 父级缺少 tests 聚合。"
    Assert-Contract ($e2eParentObj.phases.e2e.tests -eq ($expectedE2eCount * 2)) "e2e 父级 tests 未汇总所有已运行 spec（期望 $($expectedE2eCount * 2)）。"
    Assert-Contract ($e2eParentObj.phases.e2e.failures -eq 0) "e2e 父级 failures 应为 0。"
    Assert-Contract ($e2eParentObj.phases.e2e.skipped -eq 0) "e2e 父级 skipped 应为 0。"
    Assert-Contract ($e2eParentObj.phases.e2e.status -eq "passed") "e2e 父级未标记 passed。"

    $env:HARDENING_CONTRACT_FAIL_SPEC = "isolated-stack.spec.ts"
    Set-ContractMode -Mode "e2e-spec-failure"
    $e2eParentFail = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    $env:HARDENING_CONTRACT_FAIL_SPEC = $null
    $e2eParentFailObj = ConvertFrom-OutputJson -Text $e2eParentFail.text
    Assert-Contract ($null -ne $e2eParentFailObj) "e2e 父级失败场景未输出摘要。"
    Assert-Contract ($e2eParentFailObj.phases.e2e.tests -eq 4) "e2e 父级失败时未保留已运行 spec 的 tests（期望 4）。"
    Assert-Contract ($e2eParentFailObj.phases.e2e.failures -eq 1) "e2e 父级失败时未聚合 unexpected（期望 1）。"
    Assert-Contract ($e2eParentFailObj.phases.e2e.status -eq "failed") "e2e 父级失败时未标记 failed。"
    $result.checks.parent_aggregate_e2e = "passed"

    # backend 失败后 frontend/e2e 的 not-run 必须回填 preceding-phase-failed，而非空字符串。
    Set-ContractMode -Mode "pytest-failure"
    $precedingPhase = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "all")
    Assert-Contract ($precedingPhase.exit_code -eq 7) "backend 失败场景退出码未传播。"
    $precedingPhaseObj = ConvertFrom-OutputJson -Text $precedingPhase.text
    Assert-Contract ($null -ne $precedingPhaseObj) "backend 失败场景未输出摘要。"
    Assert-Contract ($precedingPhaseObj.phases.backend.status -eq "failed") "backend 阶段未标记 failed。"
    Assert-Contract ($precedingPhaseObj.phases.frontend.status -eq "not-run") "frontend 阶段未记录 not-run。"
    Assert-Contract ($precedingPhaseObj.phases.frontend.reason -eq "preceding-phase-failed") "frontend 阶段未回填 preceding-phase-failed（实际 $($precedingPhaseObj.phases.frontend.reason)）。"
    Assert-Contract ($precedingPhaseObj.phases.e2e.status -eq "not-run") "e2e 阶段未记录 not-run。"
    Assert-Contract ($precedingPhaseObj.phases.e2e.reason -eq "preceding-phase-failed") "e2e 阶段未回填 preceding-phase-failed（实际 $($precedingPhaseObj.phases.e2e.reason)）。"
    $result.checks.preceding_phase_reason = "passed"

    # JUnit 全 skip 不得判为通过。
    Set-ContractMode -Mode "junit-all-skipped"
    $junitAllSkipped = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "backend", "-Tests", "tests/test_health.py")
    Assert-Contract ($junitAllSkipped.exit_code -ne 0) "JUnit 全 skip 被误判为通过。"
    $junitAllSkippedObj = ConvertFrom-OutputJson -Text $junitAllSkipped.text
    Assert-Contract ($null -ne $junitAllSkippedObj) "JUnit 全 skip 场景未输出摘要。"
    Assert-Contract ($junitAllSkippedObj.phases.backend.stages.legacy.status -eq "failed") "JUnit 全 skip 未在 legacy 阶段标记 failed。"
    Assert-Contract ($junitAllSkippedObj.phases.backend.stages.legacy.skipped -eq 3) "JUnit 全 skip 阶段未记录 skipped=3。"
    $result.checks.junit_all_skipped = "passed"

    # Playwright stats 必须四字段完整且全为非负整数；skipped/flaky/unexpected 任一非零不通过。
    Set-ContractMode -Mode "e2e-stats-incomplete"
    $statsIncomplete = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    Assert-Contract ($statsIncomplete.exit_code -ne 0) "Playwright stats 缺字段被误判为通过。"
    $statsIncompleteObj = ConvertFrom-OutputJson -Text $statsIncomplete.text
    Assert-Contract ($null -ne $statsIncompleteObj) "stats 缺字段场景未输出摘要。"
    Assert-Contract ($statsIncompleteObj.phases.e2e.status -eq "failed") "stats 缺字段未使 e2e 阶段 failed。"
    Assert-Contract ($statsIncompleteObj.phases.e2e.stages."current-version-hardening".report_diagnostic -eq "invalid") "stats 缺字段未标记报告无效。"
    Assert-Contract (@(Read-PlaywrightCalls).Count -eq 1) "stats 缺字段后未 fail-fast。"

    Set-ContractMode -Mode "e2e-stats-skipped"
    $statsSkipped = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    Assert-Contract ($statsSkipped.exit_code -ne 0) "Playwright skipped 非零被误判为通过。"
    $statsSkippedObj = ConvertFrom-OutputJson -Text $statsSkipped.text
    Assert-Contract ($statsSkippedObj.phases.e2e.stages."current-version-hardening".status -eq "failed") "stats skipped 非零未使首个 spec failed。"
    Assert-Contract ($statsSkippedObj.phases.e2e.stages."current-version-hardening".skipped -eq 1) "未记录 skipped=1。"

    Set-ContractMode -Mode "e2e-stats-flaky"
    $statsFlaky = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e")
    Assert-Contract ($statsFlaky.exit_code -ne 0) "Playwright flaky 非零被误判为通过。"
    $statsFlakyObj = ConvertFrom-OutputJson -Text $statsFlaky.text
    Assert-Contract ($statsFlakyObj.phases.e2e.stages."current-version-hardening".status -eq "failed") "stats flaky 非零未使首个 spec failed。"
    $result.checks.playwright_stats_complete = "passed"

    # 成功 E2E stage 必须记录 exit_code=0。
    Set-ContractMode -Mode "normal"
    $e2eSuccessExit = Invoke-Entry -Script $e2eProbeEntryScript -Arguments @("-Suite", "e2e", "-Tests", "tests/e2e/task-9-suppliers.spec.ts")
    Assert-Contract ($e2eSuccessExit.exit_code -eq 0) "单 spec E2E 正常场景未成功。"
    $e2eSuccessExitObj = ConvertFrom-OutputJson -Text $e2eSuccessExit.text
    Assert-Contract ($null -ne $e2eSuccessExitObj) "单 spec E2E 场景未输出摘要。"
    $successStageProperty = $e2eSuccessExitObj.phases.e2e.stages.psobject.Properties["task-9-suppliers"]
    Assert-Contract ($null -ne $successStageProperty) "单 spec E2E 摘要缺少 task-9-suppliers stage。"
    Assert-Contract ($successStageProperty.Value.status -eq "passed") "单 spec E2E stage 未标记 passed。"
    Assert-Contract ($successStageProperty.Value.exit_code -eq 0) "成功 E2E stage 的 exit_code 应为 0（实际 $($successStageProperty.Value.exit_code)）。"
    $result.checks.e2e_success_exit_code = "passed"

    # 镜像构建成功但身份校验失败时，仍须删除本轮唯一 tag。
    Set-ContractMode -Mode "image-identity-mismatch"
    $identityFailure = Invoke-Entry -Script $probeEntryScript -Arguments @("-Suite", "backend", "-Tests", "tests/test_health.py")
    Assert-Contract ($identityFailure.exit_code -ne 0) "镜像身份校验失败未使入口失败。"
    $identityCalls = @(Read-DockerCalls)
    $imageRmCalls = @($identityCalls | Where-Object { (@($_.arguments) -contains "image") -and (@($_.arguments) -contains "rm") })
    Assert-Contract ($imageRmCalls.Count -eq 1) "镜像身份校验失败后未删除本轮唯一镜像 tag（期望 1 次 image rm，实际 $($imageRmCalls.Count)）。"
    Assert-Contract (
        @($imageRmCalls[0].arguments | Where-Object { $_ -like "supplierriskmonitoring-hardening-test:*" }).Count -eq 1
    ) "image rm 未使用本轮唯一 tag。"
    $identityObj = ConvertFrom-OutputJson -Text $identityFailure.text
    Assert-Contract ($null -ne $identityObj) "镜像身份校验失败场景未输出摘要。"
    Assert-Contract ($identityObj.cleanup.image_removed -eq $true) "镜像身份校验失败后摘要未记录镜像已删除。"
    $result.checks.image_cleanup_on_identity_failure = "passed"
}
catch {
    $result.error = $_.Exception.Message
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    $env:PATH = $originalPath
    $env:HARDENING_CONTRACT_MODE = $originalMode
    $env:HARDENING_CONTRACT_DOCKER_LOG = $originalLog
    $env:HARDENING_CONTRACT_STACK_MARKER = $originalMarker
    $env:HARDENING_CONTRACT_NPM_LOG = $originalNpmLog
    $env:HARDENING_CONTRACT_PLAYWRIGHT_LOG = $originalPlaywrightLog
    $env:HARDENING_CONTRACT_FAIL_SPEC = $originalFailSpec
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $probeEntryScript -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $frontendProbeEntryScript -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $e2eProbeEntryScript -Force -ErrorAction SilentlyContinue
    $result.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    [System.IO.Directory]::CreateDirectory($contractEvidenceDirectory) | Out-Null
    $contractJson = $result | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText(
        $contractEvidencePath,
        "$contractJson$([Environment]::NewLine)",
        [System.Text.UTF8Encoding]::new($false)
    )
    [Console]::Out.WriteLine($contractJson)
}
