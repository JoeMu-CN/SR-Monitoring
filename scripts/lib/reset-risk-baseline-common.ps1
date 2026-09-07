function Get-ResetRiskBaselineSettings {
    return [ordered]@{
        allowed_database_name = "supplier_risk_reset_test"
        required_confirm_phrase = "RESET RISK BASELINE supplier_risk_reset_test"
        summary_schema = "supplier-risk-monitoring/risk-baseline-reset-orchestrator/v1"
        restore_receipt_schema = "supplier-risk-monitoring/risk-baseline-restore-receipt/v1"
        preflight_schema = "supplier-risk-monitoring/risk-baseline-preflight/v1"
        fk_allowlist_policy = "supplier-risk-monitoring/risk-baseline-fk-allowlist/v1"
        writer_service_names = @("app", "scheduler", "research-worker")
        critical_count_names = @(
            "raw_signals",
            "ai_analysis_records",
            "risk_events",
            "risk_alerts",
            "notification_deliveries",
            "notification_runtime_state",
            "collection_runs",
            "source_member_states",
            "research_claims",
            "research_claims_promoted_nonnull"
        )
        delete_table_names = @(
            "notification_deliveries",
            "risk_alerts",
            "supplier_event_matches",
            "risk_event_signals",
            "event_entities",
            "event_locations",
            "risk_events",
            "ai_analysis_records",
            "raw_signals",
            "collection_runs",
            "source_member_states"
        )
        required_fk_edges = @(
            "notification_deliveries.alert_id -> risk_alerts.id ON DELETE SET NULL",
            "research_claims.promoted_signal_id -> raw_signals.id ON DELETE SET NULL"
        )
    }
}

function Get-ResetRiskBaselineObjectValue {
    param(
        [AllowNull()]
        [object]$Object,

        [Parameter(Mandatory)]
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }

    if ($Object -is [System.Collections.IDictionary]) {
        foreach ($key in $Object.Keys) {
            if ([string]::Equals([string]$key, $Name, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $Object[$key]
            }
        }
        return $null
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) {
        return $property.Value
    }
    return $null
}

function ConvertTo-ResetRiskBaselineRepositoryPath {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$RepositoryRoot
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "路径不能为空。"
    }

    if ([System.IO.Path]::IsPathFullyQualified($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $Path))
}

function Test-ResetRiskBaselinePathWithin {
    param(
        [Parameter(Mandatory)]
        [string]$Candidate,

        [Parameter(Mandatory)]
        [string]$Root
    )

    $trimCharacters = @(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $canonicalRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd($trimCharacters)
    $canonicalCandidate = [System.IO.Path]::GetFullPath($Candidate)
    $rootPrefix = "$canonicalRoot$([System.IO.Path]::DirectorySeparatorChar)"
    return $canonicalCandidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-ResetRiskBaselineAllowedPath {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string[]]$AllowedRoots,

        [Parameter(Mandatory)]
        [string]$Label
    )

    foreach ($allowedRoot in $AllowedRoots) {
        if (Test-ResetRiskBaselinePathWithin -Candidate $Path -Root $allowedRoot) {
            return
        }
    }
    throw "拒绝使用受控工作区外的${Label}路径。"
}

function Get-ResetRiskBaselineRepositoryRelativePath {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$RepositoryRoot
    )

    return [System.IO.Path]::GetRelativePath($RepositoryRoot, $Path)
}

function Write-ResetRiskBaselineJsonFileWithoutBom {
    param(
        [Parameter(Mandatory)]
        [object]$Value,

        [Parameter(Mandatory)]
        [string]$Path
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    $json = $Value | ConvertTo-Json -Depth 32
    [System.IO.File]::WriteAllText(
        $Path,
        "$json$([Environment]::NewLine)",
        [System.Text.UTF8Encoding]::new($false)
    )
    return $json
}

function Get-ResetRiskBaselineComposeWriterStatus {
    param(
        [Parameter(Mandatory)]
        [string]$ProjectName,

        [Parameter(Mandatory)]
        [string[]]$WriterServiceNames
    )

    $services = [ordered]@{}
    foreach ($serviceName in $WriterServiceNames) {
        $services[$serviceName] = [ordered]@{
            status = "not-found"
            stopped = $true
        }
    }

    try {
        $composeArguments = @(
            "compose",
            "--project-name",
            $ProjectName,
            "ps",
            "--all",
            "--format",
            "json"
        )
        $rawOutput = @(& docker @composeArguments 2>$null)
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose ps 失败。"
        }

        $jsonText = (($rawOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
        if (-not [string]::IsNullOrWhiteSpace($jsonText)) {
            $parsed = $jsonText | ConvertFrom-Json
            $entries = if ($parsed -is [System.Array]) { $parsed } else { @($parsed) }
            foreach ($entry in $entries) {
                $serviceName = [string](Get-ResetRiskBaselineObjectValue -Object $entry -Name "Service")
                if ($WriterServiceNames -notcontains $serviceName) {
                    continue
                }

                $state = [string](Get-ResetRiskBaselineObjectValue -Object $entry -Name "State")
                if ([string]::IsNullOrWhiteSpace($state)) {
                    $state = [string](Get-ResetRiskBaselineObjectValue -Object $entry -Name "Status")
                }
                $isActive = $state.Trim().ToLowerInvariant() -match "^(running|restarting|paused)"
                $services[$serviceName] = [ordered]@{
                    status = if ([string]::IsNullOrWhiteSpace($state)) { "unknown" } else { $state }
                    stopped = -not $isActive
                }
            }
        }

        return [ordered]@{
            available = $true
            all_stopped = @($services.Values | Where-Object { -not $_.stopped }).Count -eq 0
            services = $services
        }
    }
    catch {
        return [ordered]@{
            available = $false
            all_stopped = $false
            services = $services
        }
    }
}

function Invoke-ResetRiskBaselineCore {
    param(
        [Parameter(Mandatory)]
        [ValidateSet("preflight", "execute")]
        [string]$Operation,

        [Parameter(Mandatory)]
        [string]$ProjectName,

        [Parameter(Mandatory)]
        [string]$TargetEnvironment,

        [Parameter(Mandatory)]
        [string]$TargetDatabaseName,

        [string]$ExpectedFingerprint,

        [string]$ExpectedPlanSha256,

        [string]$ExactConfirmPhrase,

        [string]$BackupPath,

        [string]$RestoreReceiptPath
    )

    $composeArguments = @(
        "compose",
        "--project-name",
        $ProjectName,
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY"
    )
    if ($Operation -eq "execute") {
        if ([string]::IsNullOrWhiteSpace($BackupPath) -or [string]::IsNullOrWhiteSpace($RestoreReceiptPath)) {
            throw "执行核心前缺少受控备份或恢复收据路径。"
        }
        $backupMount = "$BackupPath`:/run/risk-baseline/backup:ro"
        $receiptMount = "$RestoreReceiptPath`:/run/risk-baseline/restore-receipt.json:ro"
        $composeArguments += @(
            "--volume",
            $backupMount,
            "--volume",
            $receiptMount
        )
    }
    $composeArguments += @(
        "--entrypoint",
        "python",
        "app",
        "-m",
        "app.maintenance.reset_risk_baseline",
        $Operation,
        "--environment",
        $TargetEnvironment,
        "--database-name",
        $TargetDatabaseName,
        "--output",
        "json"
    )
    if ($Operation -eq "execute") {
        $composeArguments += @(
            "--expected-fingerprint",
            $ExpectedFingerprint,
            "--expected-plan-sha256",
            $ExpectedPlanSha256,
            "--confirm-phrase",
            $ExactConfirmPhrase,
            "--backup-path",
            "/run/risk-baseline/backup",
            "--restore-receipt-path",
            "/run/risk-baseline/restore-receipt.json",
            "--active-writer-count",
            "0"
        )
    }

    try {
        # 所有路径和用户值都作为独立的原生参数传递，不进入 shell 解析。
        $rawOutput = @(& docker @composeArguments 2>$null)
        if ($LASTEXITCODE -ne 0) {
            throw "核心命令返回非零退出码。"
        }
        $jsonText = (($rawOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
        if ([string]::IsNullOrWhiteSpace($jsonText)) {
            throw "核心命令未返回 JSON。"
        }
        $payload = $jsonText | ConvertFrom-Json -AsHashtable -Depth 32
        if ($payload -isnot [System.Collections.IDictionary]) {
            throw "核心命令返回的 JSON 根节点不是对象。"
        }
        return $payload
    }
    catch {
        throw "后端 Python 核心 $Operation 不可用或未通过；未允许执行破坏性操作。"
    }
}

function Enter-ResetRiskBaselineMaintenanceLock {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$TargetEnvironment,

        [Parameter(Mandatory)]
        [string]$TargetDatabaseName
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    try {
        $stream = [System.IO.FileStream]::new(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
    }
    catch {
        throw "维护锁已存在或无法独占获取。"
    }

    try {
        $payload = [ordered]@{
            schema = "supplier-risk-monitoring/risk-baseline-maintenance-lock/v1"
            created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            environment = $TargetEnvironment
            database_name = $TargetDatabaseName
            process_id = $PID
        }
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
            "$($payload | ConvertTo-Json -Depth 4)$([Environment]::NewLine)"
        )
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        return $stream
    }
    catch {
        $stream.Dispose()
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Exit-ResetRiskBaselineMaintenanceLock {
    param(
        [AllowNull()]
        [System.IO.FileStream]$Stream,

        [AllowNull()]
        [string]$Path
    )

    if ($null -eq $Stream) {
        return "not-acquired"
    }

    try {
        $Stream.Dispose()
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
        }
        return "removed"
    }
    catch {
        return "cleanup-failed"
    }
}

function Get-ResetRiskBaselineSafeErrorMessage {
    param([Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$ErrorRecord)

    $message = $ErrorRecord.Exception.Message
    if ($message -match "(?i)(database_url|postgresql://|password|token|secret|\.env)") {
        return "安全门禁未通过；未调用破坏性操作。"
    }
    return $message
}

function New-ResetRiskBaselineSummary {
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings,

        [Parameter(Mandatory)]
        [string]$Mode,

        [Parameter(Mandatory)]
        [string]$Environment,

        [Parameter(Mandatory)]
        [string]$DatabaseName,

        [Parameter(Mandatory)]
        [string]$ComposeProjectName
    )

    return [ordered]@{
        schema = $Settings.summary_schema
        status = "blocked"
        mode = $Mode
        started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        target = [ordered]@{
            environment = $Environment
            database_name = $DatabaseName
            v1_allowed = $false
        }
        compose_project_name = $ComposeProjectName
        gates = [ordered]@{
            conflicting_switches = "not-run"
            target_policy = "not-run"
            backup_and_restore_receipt = "not-required"
            confirmation_phrase = "not-required"
            compose_writers = "not-run"
            maintenance_lock = "not-required"
            core_preflight = "not-run"
            fk_inventory = "not-run"
            target_fingerprint = "not-run"
            plan_sha256 = "not-run"
        }
        plan = [ordered]@{
            fk_allowlist_policy = $Settings.fk_allowlist_policy
            required_fk_edges = $Settings.required_fk_edges
            identity_sequences_reset = $false
            automatic_migration = $false
            automatic_collection = $false
            preflight_plan_sha256 = $null
        }
        evidence = [ordered]@{
            summary_file = $null
            core_preflight_file = $null
            execution_receipt_file = $null
        }
        cleanup = [ordered]@{
            maintenance_lock = "not-acquired"
        }
        error = $null
    }
}
