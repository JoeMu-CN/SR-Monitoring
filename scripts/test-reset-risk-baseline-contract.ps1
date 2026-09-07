[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$entryScript = Join-Path $PSScriptRoot "reset-risk-baseline.ps1"
$evidenceRoot = Join-Path $repoRoot ".omo/evidence/risk-signal-validity/task-12"
$failureDirectory = Join-Path $evidenceRoot "failure"
$happyDirectory = Join-Path $evidenceRoot "happy"
$testDirectory = Join-Path $evidenceRoot "contract-test"
$backupsRoot = Join-Path $repoRoot "backups"
$targetDatabaseName = "supplier_risk_reset_test"
$fingerprint = ("a" * 64) -join ""
$planSha256 = ("b" * 64) -join ""
$provenanceSha256 = ("c" * 64) -join ""
$results = [ordered]@{
    schema = "supplier-risk-monitoring/risk-baseline-contract-test/v1"
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    parameter_conflict = "not-run"
    production_block = "not-run"
    core_unavailable = "not-run"
    simulated_preflight = "not-run"
    execute_argument_contract = "not-run"
    real_isolated_preflight = "not-run"
    error = $null
}

function Assert-Contract {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-EntryScript {
    param(
        [Parameter(Mandatory)][string[]]$Arguments
    )

    $output = @(& pwsh -NoProfile -File $entryScript @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $text = (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
    $start = $text.IndexOf('{')
    $end = $text.LastIndexOf('}')
    if ($start -lt 0 -or $end -le $start) {
        throw "入口脚本未输出 JSON 摘要。原始输出：$text"
    }
    $summary = $text.Substring($start, $end - $start + 1) | ConvertFrom-Json -AsHashtable -Depth 32
    return [ordered]@{
        exit_code = $exitCode
        summary = $summary
        text = $text
    }
}

function Get-ArgumentAfter {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Option
    )

    $index = [Array]::IndexOf($Arguments, $Option)
    if ($index -lt 0 -or $index -ge ($Arguments.Count - 1)) {
        throw "缺少参数 $Option。"
    }
    return $Arguments[$index + 1]
}

function Write-JsonWithoutBom {
    param(
        [Parameter(Mandatory)][object]$Value,
        [Parameter(Mandatory)][string]$Path
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText(
        $Path,
        "$($Value | ConvertTo-Json -Depth 32)$([Environment]::NewLine)",
        [System.Text.UTF8Encoding]::new($false)
    )
}

New-Item -ItemType Directory -Force -Path $failureDirectory, $happyDirectory, $testDirectory, $backupsRoot | Out-Null

$priorHappyLog = Join-Path $happyDirectory "script-dry-run.txt"
if (Test-Path -LiteralPath $priorHappyLog) {
    Move-Item -LiteralPath $priorHappyLog -Destination (Join-Path $failureDirectory "inconclusive-script-dry-run-no-preflight.txt") -Force
}
foreach ($fileName in @("reset-risk-baseline-summary.json", "reset-risk-baseline-preflight.json")) {
    $priorHappyFile = Join-Path $happyDirectory $fileName
    if (Test-Path -LiteralPath $priorHappyFile) {
        Move-Item -LiteralPath $priorHappyFile -Destination (Join-Path $failureDirectory "inconclusive-$fileName") -Force
    }
}

$fakeDockerDirectory = Join-Path $testDirectory "fake-docker"
$fakeDockerScript = Join-Path $fakeDockerDirectory "docker.ps1"
$callLog = Join-Path $testDirectory "docker-calls.jsonl"
$backupPath = Join-Path $backupsRoot "task-12-contract-fixture.backup"
$receiptPath = Join-Path $backupsRoot "task-12-contract-fixture.restore-receipt.json"
$previousPath = $env:PATH
$previousMode = $env:RESET_RISK_FAKE_DOCKER_MODE
$previousLog = $env:RESET_RISK_FAKE_DOCKER_LOG
$previousPreflight = $env:RESET_RISK_FAKE_PREFLIGHT_PATH
$previousExecution = $env:RESET_RISK_FAKE_EXECUTION_PATH

try {
    New-Item -ItemType Directory -Force -Path $fakeDockerDirectory | Out-Null
    [System.IO.File]::WriteAllBytes($backupPath, [System.Text.UTF8Encoding]::new($false).GetBytes("task-12-contract-backup"))
    $backupItem = Get-Item -LiteralPath $backupPath
    $backupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backupPath).Hash.ToLowerInvariant()

    $criticalCounts = [ordered]@{
        raw_signals = 1
        ai_analysis_records = 1
        risk_events = 1
        risk_alerts = 1
        notification_deliveries = 2
        notification_runtime_state = 0
        collection_runs = 1
        source_member_states = 1
        research_claims = 1
        research_claims_promoted_nonnull = 1
    }
    $sourceTarget = [ordered]@{
        environment = "test"
        database_name = $targetDatabaseName
        fingerprint = $fingerprint
        migration_version = "0048_source_membership_state"
        critical_table_counts = $criticalCounts
    }
    $restoredTarget = [ordered]@{
        environment = "test"
        database_name = "supplier_risk_restore_contract"
        fingerprint = (("d" * 64) -join "")
        migration_version = "0048_source_membership_state"
        critical_table_counts = $criticalCounts
    }
    $restoreReceipt = [ordered]@{
        schema = "supplier-risk-monitoring/risk-baseline-restore-receipt/v1"
        status = "passed"
        backup = [ordered]@{
            sha256 = $backupHash
            size_bytes = $backupItem.Length
        }
        source_target = $sourceTarget
        restored_target = $restoredTarget
    }
    Write-JsonWithoutBom -Value $restoreReceipt -Path $receiptPath

    $preflightPayload = [ordered]@{
        schema = "supplier-risk-monitoring/risk-baseline-preflight/v1"
        status = "passed"
        mode = "preflight"
        target = $sourceTarget
        plan_sha256 = $planSha256
        fk_inventory = [ordered]@{
            policy = "supplier-risk-monitoring/risk-baseline-fk-allowlist/v1"
            status = "passed"
            observed_edges = @(
                "notification_deliveries.alert_id -> risk_alerts.id ON DELETE SET NULL",
                "research_claims.promoted_signal_id -> raw_signals.id ON DELETE SET NULL"
            )
            missing_edges = @()
            unexpected_edges = @()
        }
    }
    $executionPayload = [ordered]@{
        status = "completed"
        dry_run = $false
        environment = "test"
        database_name = $targetDatabaseName
        database_fingerprint_sha256 = $fingerprint
        plan_sha256 = $planSha256
        deleted_counts = [ordered]@{
            notification_deliveries = 2
            risk_alerts = 1
            supplier_event_matches = 1
            risk_event_signals = 1
            event_entities = 1
            event_locations = 1
            risk_events = 1
            ai_analysis_records = 1
            raw_signals = 1
            collection_runs = 1
            source_member_states = 1
        }
        preserved_counts = [ordered]@{
            research_claims = 1
            data_sources = 1
            suppliers = 1
        }
        claim_count = 1
        promoted_claim_count = 0
        claim_provenance_sha256 = $provenanceSha256
    }
    $preflightPath = Join-Path $testDirectory "preflight.json"
    $executionPath = Join-Path $testDirectory "execution.json"
    Write-JsonWithoutBom -Value $preflightPayload -Path $preflightPath
    Write-JsonWithoutBom -Value $executionPayload -Path $executionPath

    $fakeDockerContent = @'
$record = [ordered]@{ arguments = @($args) }
[System.IO.File]::AppendAllText(
    $env:RESET_RISK_FAKE_DOCKER_LOG,
    "$($record | ConvertTo-Json -Compress -Depth 8)$([Environment]::NewLine)",
    [System.Text.UTF8Encoding]::new($false)
)
if ($args -contains "ps") {
    "[]"
    $global:LASTEXITCODE = 0
    return
}
if ($args -contains "preflight") {
    if ($env:RESET_RISK_FAKE_DOCKER_MODE -eq "core-unavailable") {
        [Console]::Error.WriteLine("fake-core-unavailable")
        $global:LASTEXITCODE = 1
        return
    }
    [System.IO.File]::ReadAllText($env:RESET_RISK_FAKE_PREFLIGHT_PATH, [System.Text.UTF8Encoding]::new($false))
    $global:LASTEXITCODE = 0
    return
}
if ($args -contains "execute") {
    [System.IO.File]::ReadAllText($env:RESET_RISK_FAKE_EXECUTION_PATH, [System.Text.UTF8Encoding]::new($false))
    $global:LASTEXITCODE = 0
    return
}
[Console]::Error.WriteLine("fake-docker-unexpected-command")
$global:LASTEXITCODE = 1
'@
    [System.IO.File]::WriteAllText($fakeDockerScript, $fakeDockerContent, [System.Text.UTF8Encoding]::new($false))
    $env:PATH = "$fakeDockerDirectory;$previousPath"
    $env:RESET_RISK_FAKE_DOCKER_LOG = $callLog
    $env:RESET_RISK_FAKE_PREFLIGHT_PATH = $preflightPath
    $env:RESET_RISK_FAKE_EXECUTION_PATH = $executionPath

    $conflict = Invoke-EntryScript -Arguments @(
        "-Environment", "test", "-DatabaseName", $targetDatabaseName, "-DryRun", "-Execute",
        "-EvidenceDirectory", ".omo/evidence/risk-signal-validity/task-12/contract-test/conflict"
    )
    Assert-Contract -Condition ($conflict.exit_code -eq 2) -Message "参数冲突未返回 exit 2。"
    $results.parameter_conflict = "passed"

    $production = Invoke-EntryScript -Arguments @(
        "-Environment", "production", "-DatabaseName", $targetDatabaseName, "-Execute",
        "-EvidenceDirectory", ".omo/evidence/risk-signal-validity/task-12/contract-test/production"
    )
    Assert-Contract -Condition ($production.exit_code -eq 2) -Message "production 未硬阻断。"
    $results.production_block = "passed"

    $env:RESET_RISK_FAKE_DOCKER_MODE = "core-unavailable"
    $unavailable = Invoke-EntryScript -Arguments @(
        "-Environment", "test", "-DatabaseName", $targetDatabaseName, "-DryRun",
        "-EvidenceDirectory", ".omo/evidence/risk-signal-validity/task-12/contract-test/core-unavailable"
    )
    Assert-Contract -Condition ($unavailable.exit_code -eq 2) -Message "核心 preflight 不可用时未返回 exit 2。"
    Assert-Contract -Condition ($unavailable.summary.status -eq "blocked") -Message "核心不可用结果未标记 blocked。"
    $results.core_unavailable = "passed"

    $env:RESET_RISK_FAKE_DOCKER_MODE = "available"
    $simulated = Invoke-EntryScript -Arguments @(
        "-Environment", "test", "-DatabaseName", $targetDatabaseName, "-DryRun",
        "-EvidenceDirectory", ".omo/evidence/risk-signal-validity/task-12/contract-test/simulated-preflight"
    )
    if ($simulated.exit_code -ne 0) {
        [Console]::Out.WriteLine("=== simulated output ===")
        [Console]::Out.WriteLine($simulated.text)
        [Console]::Out.WriteLine("=== end ===")
    }
    Assert-Contract -Condition ($simulated.exit_code -eq 0) -Message "合规 preflight 未返回 exit 0。"
    Assert-Contract -Condition ($simulated.summary.gates.plan_sha256 -eq "passed") -Message "dry-run 未验证 plan_sha256。"
    $results.simulated_preflight = "passed"

    $execution = Invoke-EntryScript -Arguments @(
        "-Environment", "test", "-DatabaseName", $targetDatabaseName, "-Execute",
        "-BackupPath", $backupPath,
        "-RestoreReceiptPath", $receiptPath,
        "-ConfirmPhrase", "RESET RISK BASELINE supplier_risk_reset_test",
        "-MaintenanceLockPath", ".omo/evidence/risk-signal-validity/task-12/contract-test/execute.lock",
        "-EvidenceDirectory", ".omo/evidence/risk-signal-validity/task-12/contract-test/execute"
    )
    Assert-Contract -Condition ($execution.exit_code -eq 0) -Message "合规 execute 模拟未返回 exit 0。"

    $records = @(
        Get-Content -LiteralPath $callLog | ForEach-Object { $_ | ConvertFrom-Json -AsHashtable -Depth 8 }
    )
    $executeRecord = @($records | Where-Object { $_.arguments -contains "execute" })[-1]
    $arguments = [string[]]$executeRecord.arguments
    $backupMount = "$backupPath`:/run/risk-baseline/backup:ro"
    $receiptMount = "$receiptPath`:/run/risk-baseline/restore-receipt.json:ro"
    Assert-Contract -Condition ((@($arguments | Where-Object { $_ -eq "--volume" }).Count -eq 2)) -Message "execute 未传递两个只读 volume。"
    Assert-Contract -Condition ((Get-ArgumentAfter -Arguments $arguments -Option "--expected-fingerprint") -eq $fingerprint) -Message "execute 未传递预检指纹。"
    Assert-Contract -Condition ((Get-ArgumentAfter -Arguments $arguments -Option "--expected-plan-sha256") -eq $planSha256) -Message "execute 未传递预检 plan_sha256。"
    Assert-Contract -Condition ((Get-ArgumentAfter -Arguments $arguments -Option "--backup-path") -eq "/run/risk-baseline/backup") -Message "execute 未传递容器内备份路径。"
    Assert-Contract -Condition ((Get-ArgumentAfter -Arguments $arguments -Option "--restore-receipt-path") -eq "/run/risk-baseline/restore-receipt.json") -Message "execute 未传递容器内恢复收据路径。"
    Assert-Contract -Condition ((Get-ArgumentAfter -Arguments $arguments -Option "--active-writer-count") -eq "0") -Message "execute 未传递 active-writer-count 0。"
    Assert-Contract -Condition ($arguments -contains $backupMount) -Message "execute 备份 volume 不是只读挂载。"
    Assert-Contract -Condition ($arguments -contains $receiptMount) -Message "execute 收据 volume 不是只读挂载。"
    $results.execute_argument_contract = "passed"

    $env:PATH = $previousPath
    $realEvidenceDirectory = ".omo/evidence/risk-signal-validity/task-12/failure/inconclusive-real-preflight"
    $real = Invoke-EntryScript -Arguments @(
        "-Environment", "test", "-DatabaseName", $targetDatabaseName, "-DryRun",
        "-EvidenceDirectory", $realEvidenceDirectory
    )
    if ($real.exit_code -eq 0) {
        [System.IO.File]::WriteAllText(
            (Join-Path $happyDirectory "script-dry-run.txt"),
            "$($real.text)$([Environment]::NewLine)",
            [System.Text.UTF8Encoding]::new($false)
        )
        $results.real_isolated_preflight = "passed"
    }
    else {
        [System.IO.File]::WriteAllText(
            (Join-Path $failureDirectory "inconclusive-real-preflight.txt"),
            "$($real.text)$([Environment]::NewLine)",
            [System.Text.UTF8Encoding]::new($false)
        )
        $results.real_isolated_preflight = "inconclusive"
    }
}
catch {
    $results.error = $_.Exception.Message
    throw
}
finally {
    $env:PATH = $previousPath
    $env:RESET_RISK_FAKE_DOCKER_MODE = $previousMode
    $env:RESET_RISK_FAKE_DOCKER_LOG = $previousLog
    $env:RESET_RISK_FAKE_PREFLIGHT_PATH = $previousPreflight
    $env:RESET_RISK_FAKE_EXECUTION_PATH = $previousExecution
    Remove-Item -LiteralPath $fakeDockerDirectory -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backupPath, $receiptPath -Force -ErrorAction SilentlyContinue
    $results.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    Write-JsonWithoutBom -Value $results -Path (Join-Path $testDirectory "contract-test.json")
}

if ($results.real_isolated_preflight -eq "inconclusive") {
    [Console]::Out.WriteLine("契约测试通过；真实隔离 preflight 未完成，详见证据。")
}
else {
    [Console]::Out.WriteLine("契约测试与真实隔离 preflight 通过。")
}
