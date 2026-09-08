[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$entryScript = Join-Path $PSScriptRoot "test-current-version-hardening.ps1"
$contractRunId = "contract-{0}-{1}" -f ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), [Guid]::NewGuid().ToString("N").Substring(0, 8)
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
$stackMarker = Join-Path $temporaryRoot "stack-created.marker"
$originalPath = $env:PATH
$originalMode = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_MODE", "Process")
$originalLog = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_DOCKER_LOG", "Process")
$originalMarker = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_STACK_MARKER", "Process")
$originalNpmLog = [Environment]::GetEnvironmentVariable("HARDENING_CONTRACT_NPM_LOG", "Process")

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
}

function Read-DockerCalls {
    if (-not (Test-Path -LiteralPath $dockerCallLog)) { return @() }
    return @(
        Get-Content -LiteralPath $dockerCallLog |
            Where-Object { $_.Trim() -ne "" } |
            ForEach-Object { $_ | ConvertFrom-Json -AsHashtable }
    )
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
        $env:HARDENING_SOURCE_FINGERPRINT
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
    switch ($mode) {
        "pytest-failure" {
            [Console]::Error.WriteLine("contract-forced-pytest-failure")
            $global:LASTEXITCODE = 7
            return
        }
        "junit-failure" {
            $cases = '<testcase classname="contract" name="one"/><testcase classname="contract" name="two"><failure message="forced"/></testcase>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"2`" failures=`"1`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"2`" failures=`"1`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText((Join-Path $env:HARDENING_EVIDENCE_DIR "junit.xml"), $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 0
            return
        }
        "pytest-failure-with-report" {
            $cases = '<testcase classname="contract" name="one"/><testcase classname="contract" name="two"/><testcase classname="contract" name="three"/><testcase classname="contract" name="four"/><testcase classname="contract" name="five"><error message="forced"/></testcase>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"5`" failures=`"1`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"5`" failures=`"1`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText((Join-Path $env:HARDENING_EVIDENCE_DIR "junit.xml"), $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 3
            return
        }
        "pytest-failure-no-report" {
            [Console]::Error.WriteLine("contract-forced-pytest-failure-no-report")
            $global:LASTEXITCODE = 5
            return
        }
        "zero-tests" {
            $cases = ""
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"0`" failures=`"0`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"0`" failures=`"0`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText((Join-Path $env:HARDENING_EVIDENCE_DIR "junit.xml"), $junit, [System.Text.UTF8Encoding]::new($false))
            $global:LASTEXITCODE = 0
            return
        }
        default {
            $cases = '<testcase classname="contract" name="one"/><testcase classname="contract" name="two"/>'
            $junit = "<?xml version=`"1.0`"?><testsuites tests=`"2`" failures=`"0`" errors=`"0`" skipped=`"0`"><testsuite name=`"contract`" tests=`"2`" failures=`"0`" errors=`"0`" skipped=`"0`">$cases</testsuite></testsuites>"
            [System.IO.File]::WriteAllText((Join-Path $env:HARDENING_EVIDENCE_DIR "junit.xml"), $junit, [System.Text.UTF8Encoding]::new($false))
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
if ($mode -eq "e2e-missing-stats") {
    Write-Output '{"suites":[]}'
}
else {
    Write-Output '{"suites":[],"stats":{"expected":2,"unexpected":0,"flaky":0,"skipped":1}}'
}
exit 0
'@
    [System.IO.File]::WriteAllText($fakePlaywrightScript, $fakePlaywrightContent, [System.Text.UTF8Encoding]::new($false))

    $env:PATH = "$fakeNpmRoot;$fakeDockerRoot;$fakePlaywrightRoot;$originalPath"
    $env:HARDENING_CONTRACT_DOCKER_LOG = $dockerCallLog
    $env:HARDENING_CONTRACT_STACK_MARKER = $stackMarker
    $env:HARDENING_CONTRACT_NPM_LOG = $npmCallLog

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
    $result.checks.pytest_exit_code = "passed"

    Set-ContractMode -Mode "zero-tests"
    $zeroTests = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($zeroTests.exit_code -ne 0) "JUnit 零用例被伪装为成功。"
    $result.checks.zero_tests = "passed"

    Set-ContractMode -Mode "normal"
    $success = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($success.exit_code -eq 0) "非零 JUnit 合规场景未成功。"
    Assert-Contract ($success.text.Contains('"tests": 2')) "摘要未记录实际非零用例数。"
    $successCalls = Read-DockerCalls
    Assert-Contract (
        @($successCalls | Where-Object { $_.arguments -contains "down" }).Count -eq 1
    ) "本轮拥有的 Compose 栈未且仅未清理一次。"
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
    $result.checks.exit_zero_with_failures = "passed"

    Set-ContractMode -Mode "pytest-failure-with-report"
    $nonzeroWithReport = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($nonzeroWithReport.exit_code -eq 3) "非零 pytest 退出码 3 未被传播。"
    $nwrObj = ConvertFrom-OutputJson -Text $nonzeroWithReport.text
    Assert-Contract ($null -ne $nwrObj) "pytest-with-report 场景未输出机器可读摘要。"
    Assert-Contract ($nwrObj.phases.backend.status -eq "failed") "非零 pytest 且有报告时阶段未标记 failed。"
    Assert-Contract ($nwrObj.phases.backend.exit_code -eq 3) "失败阶段未记录原始退出码 3。"
    Assert-Contract ($nwrObj.phases.backend.tests -eq 5) "失败阶段未保存 JUnit 实际用例数。"
    Assert-Contract ($nwrObj.phases.backend.failures -eq 1) "失败阶段未保存 JUnit 失败数。"
    Assert-Contract ($nwrObj.phases.backend.junit_available -eq $true) "有报告却标记报告不可获得。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$nwrObj.phases.backend.started_at_utc)) "失败阶段缺少开始时间。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$nwrObj.phases.backend.finished_at_utc)) "失败阶段缺少结束时间。"
    $result.checks.nonzero_with_report = "passed"

    Set-ContractMode -Mode "pytest-failure-no-report"
    $nonzeroNoReport = Invoke-Entry -Script $probeEntryScript -Arguments @(
        "-Suite", "backend", "-Tests", "tests/test_health.py"
    )
    Assert-Contract ($nonzeroNoReport.exit_code -eq 5) "非零 pytest 退出码 5 未被传播。"
    $nnrObj = ConvertFrom-OutputJson -Text $nonzeroNoReport.text
    Assert-Contract ($null -ne $nnrObj) "pytest-no-report 场景未输出机器可读摘要。"
    Assert-Contract ($nnrObj.phases.backend.status -eq "failed") "缺报告时阶段未标记 failed。"
    Assert-Contract ($nnrObj.phases.backend.exit_code -eq 5) "缺报告阶段未记录原始退出码 5。"
    Assert-Contract ($nnrObj.phases.backend.junit_available -eq $false) "缺报告未明确记录报告不可获得。"
    Assert-Contract ($nnrObj.phases.backend.junit_diagnostic -eq "missing") "缺报告未标记 missing 诊断。"
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
    Assert-Contract ($e2eMissingObj.phases.e2e.report_available -eq $true) "Playwright 可解析报告被误判不可获得。"
    Assert-Contract ($e2eMissingObj.phases.e2e.report_diagnostic -eq "invalid") "Playwright 缺 stats 未记录报告无效。"
    $result.checks.e2e_missing_stats = "passed"
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
