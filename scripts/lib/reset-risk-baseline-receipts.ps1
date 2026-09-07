function Read-ResetRiskBaselineJsonObject {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$Label
    )

    try {
        $text = Get-Content -Raw -LiteralPath $Path
        $value = $text | ConvertFrom-Json -AsHashtable -Depth 32
        if ($value -isnot [System.Collections.IDictionary]) {
            throw "JSON 根节点不是对象。"
        }
        return $value
    }
    catch {
        throw "${Label}不是有效的 JSON 对象。"
    }
}

function Assert-ResetRiskBaselineSha256 {
    param(
        [AllowNull()]
        [object]$Value,

        [Parameter(Mandatory)]
        [string]$Label
    )

    $hash = [string]$Value
    if ($hash -notmatch "^[A-Fa-f0-9]{64}$") {
        throw "${Label}不是有效的 SHA-256 十六进制摘要。"
    }
    return $hash.ToLowerInvariant()
}

function Get-ResetRiskBaselineNonNegativeCount {
    param(
        [AllowNull()]
        [object]$Value,

        [Parameter(Mandatory)]
        [string]$Label
    )

    [long]$parsed = 0
    if ($null -eq $Value -or -not [long]::TryParse([string]$Value, [ref]$parsed) -or $parsed -lt 0) {
        throw "${Label}必须是非负整数。"
    }
    return $parsed
}

function Get-ResetRiskBaselineCriticalCounts {
    param(
        [AllowNull()]
        [object]$Counts,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings,

        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($null -eq $Counts) {
        throw "${Label}缺少关键表计数。"
    }

    $normalized = [ordered]@{}
    foreach ($countName in $Settings.critical_count_names) {
        $normalized[$countName] = Get-ResetRiskBaselineNonNegativeCount `
            -Value (Get-ResetRiskBaselineObjectValue -Object $Counts -Name $countName) `
            -Label "$Label.$countName"
    }
    return $normalized
}

function Assert-ResetRiskBaselineCountsEqual {
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Expected,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Actual,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings,

        [Parameter(Mandatory)]
        [string]$Label
    )

    foreach ($countName in $Settings.critical_count_names) {
        if ([long]$Expected[$countName] -ne [long]$Actual[$countName]) {
            throw "${Label}的关键表计数不匹配。"
        }
    }
}

function Get-ResetRiskBaselineTargetDescriptor {
    param(
        [AllowNull()]
        [object]$Target,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings,

        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($null -eq $Target) {
        throw "${Label}缺少目标描述。"
    }

    $environment = [string](Get-ResetRiskBaselineObjectValue -Object $Target -Name "environment")
    $databaseName = [string](Get-ResetRiskBaselineObjectValue -Object $Target -Name "database_name")
    $fingerprint = Assert-ResetRiskBaselineSha256 `
        -Value (Get-ResetRiskBaselineObjectValue -Object $Target -Name "fingerprint") `
        -Label "$Label.fingerprint"
    $migrationVersion = [string](Get-ResetRiskBaselineObjectValue -Object $Target -Name "migration_version")
    if ($migrationVersion -notmatch "^[0-9]{4}(?:_[a-z0-9_]+)?$") {
        throw "$Label.migration_version无效。"
    }

    return [ordered]@{
        environment = $environment
        database_name = $databaseName
        fingerprint = $fingerprint
        migration_version = $migrationVersion
        critical_table_counts = Get-ResetRiskBaselineCriticalCounts `
            -Counts (Get-ResetRiskBaselineObjectValue -Object $Target -Name "critical_table_counts") `
            -Settings $Settings `
            -Label "$Label.critical_table_counts"
    }
}

function Get-ValidatedResetRiskBaselineRestoreReceipt {
    param(
        [Parameter(Mandatory)]
        [string]$ReceiptPath,

        [Parameter(Mandatory)]
        [string]$BackupFile,

        [Parameter(Mandatory)]
        [string]$ExpectedEnvironment,

        [Parameter(Mandatory)]
        [string]$ExpectedDatabaseName,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings
    )

    $receipt = Read-ResetRiskBaselineJsonObject -Path $ReceiptPath -Label "恢复演练收据"
    if ([string](Get-ResetRiskBaselineObjectValue -Object $receipt -Name "schema") -ne $Settings.restore_receipt_schema) {
        throw "恢复演练收据 schema 不受支持。"
    }
    if ([string](Get-ResetRiskBaselineObjectValue -Object $receipt -Name "status") -ne "passed") {
        throw "恢复演练收据未标记为 passed。"
    }

    $backup = Get-ResetRiskBaselineObjectValue -Object $receipt -Name "backup"
    $receiptHash = Assert-ResetRiskBaselineSha256 `
        -Value (Get-ResetRiskBaselineObjectValue -Object $backup -Name "sha256") `
        -Label "恢复演练收据.backup.sha256"
    $receiptSize = Get-ResetRiskBaselineNonNegativeCount `
        -Value (Get-ResetRiskBaselineObjectValue -Object $backup -Name "size_bytes") `
        -Label "恢复演练收据.backup.size_bytes"
    $backupItem = Get-Item -LiteralPath $BackupFile
    if ($backupItem.Length -le 0) {
        throw "备份文件为空。"
    }
    if ($receiptSize -ne $backupItem.Length) {
        throw "恢复演练收据中的备份大小不匹配。"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $BackupFile).Hash.ToLowerInvariant()
    if ($receiptHash -ne $actualHash) {
        throw "备份 SHA-256 与恢复演练收据不匹配。"
    }

    $sourceTarget = Get-ResetRiskBaselineTargetDescriptor `
        -Target (Get-ResetRiskBaselineObjectValue -Object $receipt -Name "source_target") `
        -Settings $Settings `
        -Label "恢复演练收据.source_target"
    if ($sourceTarget.environment -ne $ExpectedEnvironment -or $sourceTarget.database_name -ne $ExpectedDatabaseName) {
        throw "恢复演练收据的源目标与本次受控目标不匹配。"
    }

    $restoredTarget = Get-ResetRiskBaselineTargetDescriptor `
        -Target (Get-ResetRiskBaselineObjectValue -Object $receipt -Name "restored_target") `
        -Settings $Settings `
        -Label "恢复演练收据.restored_target"
    if ($restoredTarget.environment -ne "test" -or $restoredTarget.database_name -notmatch "^supplier_risk_restore_[a-z0-9_]+$") {
        throw "恢复演练收据未证明使用独立恢复数据库。"
    }
    if ($restoredTarget.database_name -eq $ExpectedDatabaseName) {
        throw "恢复演练收据的恢复数据库不能等于清理目标。"
    }
    if ($restoredTarget.migration_version -ne $sourceTarget.migration_version) {
        throw "恢复演练收据的源库与恢复库迁移版本不匹配。"
    }
    Assert-ResetRiskBaselineCountsEqual `
        -Expected $sourceTarget.critical_table_counts `
        -Actual $restoredTarget.critical_table_counts `
        -Settings $Settings `
        -Label "恢复演练收据的源库与恢复库"

    return [ordered]@{
        source_target = $sourceTarget
        restored_target = $restoredTarget
        backup_sha256 = $actualHash
        backup_size_bytes = $backupItem.Length
    }
}

function ConvertTo-ResetRiskBaselineStringArray {
    param([AllowNull()][object]$Value)

    # 一元逗号防止 PowerShell 把空数组展开为 $null；StrictMode 下 $null.Count 会抛异常。
    if ($null -eq $Value) {
        return ,@()
    }
    return ,@($Value | ForEach-Object { [string]$_ })
}

function Get-ValidatedResetRiskBaselinePreflight {
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Payload,

        [Parameter(Mandatory)]
        [string]$ExpectedEnvironment,

        [Parameter(Mandatory)]
        [string]$ExpectedDatabaseName,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings
    )

    if ([string](Get-ResetRiskBaselineObjectValue -Object $Payload -Name "schema") -ne $Settings.preflight_schema) {
        throw "Python 核心预检 schema 不受支持。"
    }
    if ([string](Get-ResetRiskBaselineObjectValue -Object $Payload -Name "status") -ne "passed") {
        throw "Python 核心预检未通过。"
    }
    if ([string](Get-ResetRiskBaselineObjectValue -Object $Payload -Name "mode") -ne "preflight") {
        throw "Python 核心未声明 preflight 模式。"
    }

    $target = Get-ResetRiskBaselineTargetDescriptor `
        -Target (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "target") `
        -Settings $Settings `
        -Label "Python 核心预检.target"
    if ($target.environment -ne $ExpectedEnvironment -or $target.database_name -ne $ExpectedDatabaseName) {
        throw "Python 核心预检目标与脚本受控目标不匹配。"
    }
    $planSha256 = Assert-ResetRiskBaselineSha256 `
        -Value (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "plan_sha256") `
        -Label "Python 核心预检.plan_sha256"

    $fkInventory = Get-ResetRiskBaselineObjectValue -Object $Payload -Name "fk_inventory"
    if ($null -eq $fkInventory) {
        throw "Python 核心预检缺少 FK 盘点。"
    }
    if ([string](Get-ResetRiskBaselineObjectValue -Object $fkInventory -Name "policy") -ne $Settings.fk_allowlist_policy) {
        throw "Python 核心 FK 允许列表策略不受支持。"
    }
    if ([string](Get-ResetRiskBaselineObjectValue -Object $fkInventory -Name "status") -ne "passed") {
        throw "Python 核心 FK 盘点未通过。"
    }
    $missingEdges = ConvertTo-ResetRiskBaselineStringArray -Value (Get-ResetRiskBaselineObjectValue -Object $fkInventory -Name "missing_edges")
    $unexpectedEdges = ConvertTo-ResetRiskBaselineStringArray -Value (Get-ResetRiskBaselineObjectValue -Object $fkInventory -Name "unexpected_edges")
    if ($missingEdges.Count -ne 0 -or $unexpectedEdges.Count -ne 0) {
        throw "Python 核心检测到 FK 集合漂移。"
    }
    $observedEdges = ConvertTo-ResetRiskBaselineStringArray -Value (Get-ResetRiskBaselineObjectValue -Object $fkInventory -Name "observed_edges")
    foreach ($requiredEdge in $Settings.required_fk_edges) {
        if ($observedEdges -notcontains $requiredEdge) {
            throw "Python 核心 FK 盘点缺少必须的 SET NULL 关系。"
        }
    }

    return [ordered]@{
        target = $target
        plan_sha256 = $planSha256
        fk_inventory = $fkInventory
    }
}

function Assert-ResetRiskBaselinePreflightMatchesReceipt {
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Preflight,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$ReceiptTarget,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings
    )

    if ($Preflight.target.fingerprint -ne $ReceiptTarget.fingerprint) {
        throw "当前目标数据库指纹与成功恢复演练收据不匹配。"
    }
    if ($Preflight.target.migration_version -ne $ReceiptTarget.migration_version) {
        throw "当前目标数据库迁移版本与成功恢复演练收据不匹配。"
    }
    Assert-ResetRiskBaselineCountsEqual `
        -Expected $ReceiptTarget.critical_table_counts `
        -Actual $Preflight.target.critical_table_counts `
        -Settings $Settings `
        -Label "当前目标数据库与恢复演练收据"
}

function ConvertTo-ResetRiskBaselineCountMap {
    param(
        [AllowNull()]
        [object]$Counts,

        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($null -eq $Counts) {
        throw "${Label}缺失。"
    }

    $map = [ordered]@{}
    if ($Counts -is [System.Collections.IDictionary]) {
        foreach ($key in $Counts.Keys) {
            $map[[string]$key] = Get-ResetRiskBaselineNonNegativeCount -Value $Counts[$key] -Label "$Label.$key"
        }
        return $map
    }

    foreach ($entry in @($Counts)) {
        if ($entry -is [System.Array] -and $entry.Count -eq 2) {
            $tableName = [string]$entry[0]
            $countValue = $entry[1]
        }
        else {
            $tableName = [string](Get-ResetRiskBaselineObjectValue -Object $entry -Name "table")
            $countValue = Get-ResetRiskBaselineObjectValue -Object $entry -Name "count"
        }
        if ([string]::IsNullOrWhiteSpace($tableName) -or $map.Contains($tableName)) {
            throw "${Label}包含无效或重复表名。"
        }
        $map[$tableName] = Get-ResetRiskBaselineNonNegativeCount -Value $countValue -Label "$Label.$tableName"
    }
    return $map
}

function Get-ResetRiskBaselineRequiredValue {
    param(
        [Parameter(Mandatory)]
        [AllowNull()]
        [object]$Primary,

        [Parameter(Mandatory)]
        [AllowNull()]
        [object]$Fallback,

        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($null -ne $Primary -and -not [string]::IsNullOrWhiteSpace([string]$Primary)) {
        return $Primary
    }
    if ($null -ne $Fallback -and -not [string]::IsNullOrWhiteSpace([string]$Fallback)) {
        return $Fallback
    }
    throw "${Label}缺失。"
}

function Assert-ResetRiskBaselineExecutionReceipt {
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Payload,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Preflight,

        [Parameter(Mandatory)]
        [string]$ExpectedEnvironment,

        [Parameter(Mandatory)]
        [string]$ExpectedDatabaseName,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Settings
    )

    $status = Get-ResetRiskBaselineObjectValue -Object $Payload -Name "status"
    if ($null -ne $status -and [string]$status -ne "completed") {
        throw "Python 核心执行收据未确认完成。"
    }
    $dryRun = Get-ResetRiskBaselineObjectValue -Object $Payload -Name "dry_run"
    if ($null -ne $dryRun -and ($dryRun -isnot [bool] -or [bool]$dryRun)) {
        throw "Python 核心执行收据不是提交结果。"
    }
    $receiptEnvironment = Get-ResetRiskBaselineObjectValue -Object $Payload -Name "environment"
    if ($null -ne $receiptEnvironment -and [string]$receiptEnvironment -ne $ExpectedEnvironment) {
        throw "Python 核心执行收据环境不匹配。"
    }
    if ([string](Get-ResetRiskBaselineObjectValue -Object $Payload -Name "database_name") -ne $ExpectedDatabaseName) {
        throw "Python 核心执行收据目标数据库不匹配。"
    }

    $fingerprint = Assert-ResetRiskBaselineSha256 `
        -Value (Get-ResetRiskBaselineRequiredValue `
            -Primary (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "pre_reset_fingerprint") `
            -Fallback (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "database_fingerprint_sha256") `
            -Label "Python 核心执行收据指纹") `
        -Label "Python 核心执行收据指纹"
    if ($fingerprint -ne $Preflight.target.fingerprint) {
        throw "Python 核心执行收据未绑定本次预检指纹。"
    }
    $planSha256 = Assert-ResetRiskBaselineSha256 `
        -Value (Get-ResetRiskBaselineRequiredValue `
            -Primary (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "pre_reset_plan_sha256") `
            -Fallback (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "plan_sha256") `
            -Label "Python 核心执行收据计划摘要") `
        -Label "Python 核心执行收据计划摘要"
    if ($planSha256 -ne $Preflight.plan_sha256) {
        throw "Python 核心执行收据未绑定本次预检计划摘要。"
    }

    $deletedCounts = ConvertTo-ResetRiskBaselineCountMap `
        -Counts (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "deleted_counts") `
        -Label "Python 核心执行收据.deleted_counts"
    foreach ($tableName in $Settings.delete_table_names) {
        if (-not $deletedCounts.Contains($tableName)) {
            throw "Python 核心执行收据缺少删除计数。"
        }
    }
    foreach ($tableName in @(
        "raw_signals",
        "ai_analysis_records",
        "risk_events",
        "risk_alerts",
        "notification_deliveries",
        "collection_runs",
        "source_member_states"
    )) {
        if ([long]$deletedCounts[$tableName] -ne [long]$Preflight.target.critical_table_counts[$tableName]) {
            throw "Python 核心执行收据的删除计数与预检不一致。"
        }
    }

    $preservedCounts = ConvertTo-ResetRiskBaselineCountMap `
        -Counts (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "preserved_counts") `
        -Label "Python 核心执行收据.preserved_counts"
    if (-not $preservedCounts.Contains("research_claims")) {
        throw "Python 核心执行收据缺少 research_claims 保留摘要。"
    }
    if ([long]$preservedCounts["research_claims"] -ne [long]$Preflight.target.critical_table_counts["research_claims"]) {
        throw "Python 核心未保持 research_claims 行数。"
    }
    $claimCount = Get-ResetRiskBaselineNonNegativeCount `
        -Value (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "claim_count") `
        -Label "Python 核心执行收据.claim_count"
    if ($claimCount -ne [long]$Preflight.target.critical_table_counts["research_claims"]) {
        throw "Python 核心执行收据的 claim 摘要不匹配。"
    }
    $promotedClaimCount = Get-ResetRiskBaselineNonNegativeCount `
        -Value (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "promoted_claim_count") `
        -Label "Python 核心执行收据.promoted_claim_count"
    if ($promotedClaimCount -ne 0) {
        throw "Python 核心未将 research claim 的 promoted link 归零。"
    }
    Assert-ResetRiskBaselineSha256 `
        -Value (Get-ResetRiskBaselineObjectValue -Object $Payload -Name "claim_provenance_sha256") `
        -Label "Python 核心执行收据.claim_provenance_sha256" | Out-Null
}
