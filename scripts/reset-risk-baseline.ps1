[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("test", "staging", "production")]
    [string]$Environment,

    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9_]{0,62}$")]
    [string]$DatabaseName,

    [switch]$DryRun,
    [switch]$Execute,
    [string]$BackupPath,
    [string]$RestoreReceiptPath,
    [string]$ConfirmPhrase,
    [string]$MaintenanceLockPath,

    [ValidatePattern("^[a-z0-9][a-z0-9_-]{0,62}$")]
    [string]$ComposeProjectName = "supplierriskmonitoring",

    [string]$EvidenceDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backupsRoot = Join-Path $repoRoot "backups"
$omoRoot = Join-Path $repoRoot ".omo"
. (Join-Path $PSScriptRoot "lib/reset-risk-baseline-common.ps1")
. (Join-Path $PSScriptRoot "lib/reset-risk-baseline-receipts.ps1")
$settings = Get-ResetRiskBaselineSettings

$requestedMode = if ($Execute.IsPresent) { "execute" } else { "dry-run" }
$defaultEvidenceDirectory = Join-Path $omoRoot "evidence/risk-signal-validity/task-12"
$defaultMaintenanceLockPath = Join-Path $omoRoot "risk-baseline-rebuild.lock"
$evidenceDirectoryFull = $null
$maintenanceLockPathFull = $null
$summaryFile = $null
$preflightEvidenceFile = $null
$executionReceiptFile = $null
$lockStream = $null
$evidenceReady = $false
$exitCode = 2

$summary = New-ResetRiskBaselineSummary `
    -Settings $settings `
    -Mode $requestedMode `
    -Environment $Environment `
    -DatabaseName $DatabaseName `
    -ComposeProjectName $ComposeProjectName

try {
    if ($DryRun.IsPresent -and $Execute.IsPresent) {
        throw "拒绝同时指定 -DryRun 与 -Execute，避免含糊的破坏性意图。"
    }
    $summary.gates.conflicting_switches = if ($Execute.IsPresent) { "passed" } else { "dry-run" }

    if ($Environment -ne "test" -or $DatabaseName -ne $settings.allowed_database_name) {
        throw "v1 仅允许 Environment=test 且 DatabaseName=supplier_risk_reset_test；staging/production 永久硬阻断。"
    }
    $summary.target.v1_allowed = $true
    $summary.gates.target_policy = "passed"

    $requestedEvidenceDirectory = if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
        $defaultEvidenceDirectory
    }
    else {
        $EvidenceDirectory
    }
    $evidenceDirectoryFull = ConvertTo-ResetRiskBaselineRepositoryPath `
        -Path $requestedEvidenceDirectory `
        -RepositoryRoot $repoRoot
    Assert-ResetRiskBaselineAllowedPath `
        -Path $evidenceDirectoryFull `
        -AllowedRoots @($backupsRoot, $omoRoot) `
        -Label "证据目录"
    if (-not (Test-Path -LiteralPath $evidenceDirectoryFull)) {
        New-Item -ItemType Directory -Force -Path $evidenceDirectoryFull | Out-Null
    }
    $summaryFile = Join-Path $evidenceDirectoryFull "reset-risk-baseline-summary.json"
    $preflightEvidenceFile = Join-Path $evidenceDirectoryFull "reset-risk-baseline-preflight.json"
    $executionReceiptFile = Join-Path $evidenceDirectoryFull "reset-risk-baseline-execution-receipt.json"
    $summary.evidence.summary_file = Get-ResetRiskBaselineRepositoryRelativePath -Path $summaryFile -RepositoryRoot $repoRoot
    $summary.evidence.core_preflight_file = Get-ResetRiskBaselineRepositoryRelativePath -Path $preflightEvidenceFile -RepositoryRoot $repoRoot
    $summary.evidence.execution_receipt_file = Get-ResetRiskBaselineRepositoryRelativePath -Path $executionReceiptFile -RepositoryRoot $repoRoot
    $evidenceReady = $true

    if (-not $Execute.IsPresent) {
        # dry-run 必须真实连接指定隔离库做只读盘点；compose 不可用或写进程未停时直接阻断。
        $composeStatus = Get-ResetRiskBaselineComposeWriterStatus `
            -ProjectName $ComposeProjectName `
            -WriterServiceNames $settings.writer_service_names
        $summary.gates.compose_writers = $composeStatus
        if (-not $composeStatus.available -or -not $composeStatus.all_stopped) {
            throw "无法确认 app、scheduler、research-worker 均已停止。"
        }
        $summary.gates.compose_writers = "passed"
        $summary.plan.compose_writer_services = $composeStatus.services
        $summary.plan.compose_writers_all_stopped = $composeStatus.all_stopped
        $summary.plan.compose_writers_observed = $true

        $preflightPayload = Invoke-ResetRiskBaselineCore `
            -Operation "preflight" `
            -ProjectName $ComposeProjectName `
            -TargetEnvironment $Environment `
            -TargetDatabaseName $DatabaseName
        $preflight = Get-ValidatedResetRiskBaselinePreflight `
            -Payload $preflightPayload `
            -ExpectedEnvironment $Environment `
            -ExpectedDatabaseName $DatabaseName `
            -Settings $settings
        Write-ResetRiskBaselineJsonFileWithoutBom -Value $preflightPayload -Path $preflightEvidenceFile | Out-Null

        $summary.gates.core_preflight = "passed"
        $summary.gates.fk_inventory = "passed"
        $summary.gates.target_fingerprint = "passed"
        $summary.gates.plan_sha256 = "passed"
        $summary.plan.preflight_plan_sha256 = $preflight.plan_sha256
        $summary.status = "dry-run"
        $exitCode = 0
    }
    else {
        if ([string]::IsNullOrWhiteSpace($BackupPath) -or [string]::IsNullOrWhiteSpace($RestoreReceiptPath)) {
            throw "Execute 前必须提供 -BackupPath 与 -RestoreReceiptPath。"
        }
        if ([string]::IsNullOrWhiteSpace($ConfirmPhrase) -or -not [string]::Equals(
            $ConfirmPhrase,
            $settings.required_confirm_phrase,
            [System.StringComparison]::Ordinal
        )) {
            throw "确认短语不精确。"
        }
        $summary.gates.confirmation_phrase = "passed"

        $backupPathFull = ConvertTo-ResetRiskBaselineRepositoryPath -Path $BackupPath -RepositoryRoot $repoRoot
        Assert-ResetRiskBaselineAllowedPath -Path $backupPathFull -AllowedRoots @($backupsRoot) -Label "备份"
        if (-not (Test-Path -LiteralPath $backupPathFull -PathType Leaf)) {
            throw "备份文件不存在。"
        }
        $restoreReceiptPathFull = ConvertTo-ResetRiskBaselineRepositoryPath -Path $RestoreReceiptPath -RepositoryRoot $repoRoot
        Assert-ResetRiskBaselineAllowedPath -Path $restoreReceiptPathFull -AllowedRoots @($backupsRoot, $omoRoot) -Label "恢复演练收据"
        if (-not (Test-Path -LiteralPath $restoreReceiptPathFull -PathType Leaf)) {
            throw "恢复演练收据不存在。"
        }
        $validatedReceipt = Get-ValidatedResetRiskBaselineRestoreReceipt `
            -ReceiptPath $restoreReceiptPathFull `
            -BackupFile $backupPathFull `
            -ExpectedEnvironment $Environment `
            -ExpectedDatabaseName $DatabaseName `
            -Settings $settings
        $summary.gates.backup_and_restore_receipt = "passed"

        $requestedMaintenanceLockPath = if ([string]::IsNullOrWhiteSpace($MaintenanceLockPath)) {
            $defaultMaintenanceLockPath
        }
        else {
            $MaintenanceLockPath
        }
        $maintenanceLockPathFull = ConvertTo-ResetRiskBaselineRepositoryPath `
            -Path $requestedMaintenanceLockPath `
            -RepositoryRoot $repoRoot
        Assert-ResetRiskBaselineAllowedPath `
            -Path $maintenanceLockPathFull `
            -AllowedRoots @($backupsRoot, $omoRoot) `
            -Label "维护锁"
        $lockStream = Enter-ResetRiskBaselineMaintenanceLock `
            -Path $maintenanceLockPathFull `
            -TargetEnvironment $Environment `
            -TargetDatabaseName $DatabaseName
        $summary.gates.maintenance_lock = "acquired"

        $composeStatus = Get-ResetRiskBaselineComposeWriterStatus `
            -ProjectName $ComposeProjectName `
            -WriterServiceNames $settings.writer_service_names
        $summary.gates.compose_writers = $composeStatus
        if (-not $composeStatus.available -or -not $composeStatus.all_stopped) {
            throw "无法确认 app、scheduler、research-worker 均已停止。"
        }
        $summary.gates.compose_writers = "passed"

        $preflightPayload = Invoke-ResetRiskBaselineCore `
            -Operation "preflight" `
            -ProjectName $ComposeProjectName `
            -TargetEnvironment $Environment `
            -TargetDatabaseName $DatabaseName
        $preflight = Get-ValidatedResetRiskBaselinePreflight `
            -Payload $preflightPayload `
            -ExpectedEnvironment $Environment `
            -ExpectedDatabaseName $DatabaseName `
            -Settings $settings
        Write-ResetRiskBaselineJsonFileWithoutBom -Value $preflightPayload -Path $preflightEvidenceFile | Out-Null
        Assert-ResetRiskBaselinePreflightMatchesReceipt `
            -Preflight $preflight `
            -ReceiptTarget $validatedReceipt.source_target `
            -Settings $settings
        $summary.gates.core_preflight = "passed"
        $summary.gates.fk_inventory = "passed"
        $summary.gates.target_fingerprint = "passed"
        $summary.gates.plan_sha256 = "passed"
        $summary.plan.preflight_plan_sha256 = $preflight.plan_sha256

        $executionPayload = Invoke-ResetRiskBaselineCore `
            -Operation "execute" `
            -ProjectName $ComposeProjectName `
            -TargetEnvironment $Environment `
            -TargetDatabaseName $DatabaseName `
            -ExpectedFingerprint $preflight.target.fingerprint `
            -ExpectedPlanSha256 $preflight.plan_sha256 `
            -ExactConfirmPhrase $ConfirmPhrase `
            -BackupPath $backupPathFull `
            -RestoreReceiptPath $restoreReceiptPathFull
        Assert-ResetRiskBaselineExecutionReceipt `
            -Payload $executionPayload `
            -Preflight $preflight `
            -ExpectedEnvironment $Environment `
            -ExpectedDatabaseName $DatabaseName `
            -Settings $settings
        Write-ResetRiskBaselineJsonFileWithoutBom -Value $executionPayload -Path $executionReceiptFile | Out-Null

        $summary.status = "completed"
        $exitCode = 0
    }
}
catch {
    $summary.status = "blocked"
    $summary.error = Get-ResetRiskBaselineSafeErrorMessage -ErrorRecord $_
    $exitCode = 2
}
finally {
    $summary.cleanup.maintenance_lock = Exit-ResetRiskBaselineMaintenanceLock `
        -Stream $lockStream `
        -Path $maintenanceLockPathFull
    $summary.finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $jsonSummary = $summary | ConvertTo-Json -Depth 32
    if ($evidenceReady) {
        try {
            [System.IO.File]::WriteAllText(
                $summaryFile,
                "$jsonSummary$([Environment]::NewLine)",
                [System.Text.UTF8Encoding]::new($false)
            )
        }
        catch {
            $exitCode = 2
        }
    }
    [Console]::Out.WriteLine($jsonSummary)
}

exit $exitCode