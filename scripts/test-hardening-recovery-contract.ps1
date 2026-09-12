[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 任务11 契约测试：用 fake docker.ps1（临时 PATH 前置 + JSONL 调用日志 +
# 模式分支）驱动 scripts/test-hardening-recovery.ps1 的探针副本，锁定 9 条契约。
# 全绿 exit 0；任一失败 exit 1。证据写入任务11证据目录的 contractRunId 子目录。
# ---------------------------------------------------------------------------

$repoRoot = Split-Path -Parent $PSScriptRoot
$entryScript = Join-Path $PSScriptRoot "test-hardening-recovery.ps1"
$contractRunId = "contract-{0}-{1}" -f ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), [Guid]::NewGuid().ToString("N").Substring(0, 8)
$contractEvidenceDirectory = Join-Path $repoRoot ".omo/evidence/task-11-current-version-hardening/$contractRunId"
$contractEvidencePath = Join-Path $contractEvidenceDirectory "contract.json"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "supplier-risk-hardening-recovery-contract-{0}-{1}" -f $PID, [Guid]::NewGuid().ToString("N")
)
$fakeDockerRoot = Join-Path $temporaryRoot "fake-docker"
$fakeDockerScript = Join-Path $fakeDockerRoot "docker.ps1"
$probeEntryScript = Join-Path $PSScriptRoot (".test-hardening-recovery-contract-probe-{0}.ps1" -f [Guid]::NewGuid().ToString("N"))
$dockerCallLog = Join-Path $temporaryRoot "docker-calls.jsonl"
$stackMarker = Join-Path $temporaryRoot "stack-created.marker"
$originalPath = $env:PATH
$originalMode = [Environment]::GetEnvironmentVariable("HARDENING_RECOVERY_CONTRACT_MODE", "Process")
$originalLog = [Environment]::GetEnvironmentVariable("HARDENING_RECOVERY_CONTRACT_DOCKER_LOG", "Process")
$originalMarker = [Environment]::GetEnvironmentVariable("HARDENING_RECOVERY_CONTRACT_STACK_MARKER", "Process")

$result = [ordered]@{
    schema = "supplier-risk-monitoring/hardening-recovery-contract/v1"
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    checks = [ordered]@{
        occupied_project_rejected = "not-run"
        image_identity_mismatch = "not-run"
        existing_restore_target_rejected = "not-run"
        corrupted_backup = "not-run"
        rollback_blocked_not_failure = "not-run"
        cleanup_owner_only = "not-run"
        verify_failure_surfaced = "not-run"
        dump_sha256_recorded = "not-run"
        fail_fast = "not-run"
    }
    checks_total = 9
    checks_passed = 0
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
        [AllowEmptyCollection()][string[]]$Arguments = @(),
        [string]$Script = $probeEntryScript
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

    $env:HARDENING_RECOVERY_CONTRACT_MODE = $Mode
    Remove-Item -LiteralPath $stackMarker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $dockerCallLog -Force -ErrorAction SilentlyContinue
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

function Get-RecoveryDocumentPath {
    param([Parameter(Mandatory)]$Document)

    return (Join-Path $repoRoot (".omo/evidence/task-11-current-version-hardening/{0}/recovery.json" -f $Document.run_id))
}

function Test-NoBom {
    param([Parameter(Mandatory)][string]$Path)

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 3) { return $true }
    return -not (($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF))
}

$contractFailed = $false
try {
    if (-not (Test-Path -LiteralPath $entryScript -PathType Leaf)) {
        throw "缺少待验收入口：$entryScript"
    }
    New-Item -ItemType Directory -Force -Path $fakeDockerRoot | Out-Null

    $fakeDockerContent = @'
$argList = @($args)
$record = [ordered]@{arguments = @($argList)}
[System.IO.File]::AppendAllText(
    $env:HARDENING_RECOVERY_CONTRACT_DOCKER_LOG,
    "$($record | ConvertTo-Json -Compress)$([Environment]::NewLine)",
    [System.Text.UTF8Encoding]::new($false)
)

$mode = $env:HARDENING_RECOVERY_CONTRACT_MODE
$marker = $env:HARDENING_RECOVERY_CONTRACT_STACK_MARKER
$joined = [string]::Join(" ", $argList)
$projectLabel = "com.docker.compose.project=supplier-risk-hardening-recovery-test"
$ownerKey = "supplier-risk-hardening-recovery.owner"

if (($argList -contains "ps") -or $joined.StartsWith("network ls") -or $joined.StartsWith("volume ls")) {
    $hasProject = $joined.Contains($projectLabel)
    $hasOwner = $joined.Contains($ownerKey)
    if (($mode -eq "occupied-project") -and $hasProject -and (-not $hasOwner)) {
        "contract-foreign-resource"
        $global:LASTEXITCODE = 0
        return
    }
    if (Test-Path -LiteralPath $marker) {
        if (($mode -eq "cleanup-owner-only") -and $hasProject -and (-not $hasOwner)) {
            "contract-owned-resource"
            "contract-foreign-resource"
        }
        else {
            "contract-owned-resource"
        }
        $global:LASTEXITCODE = 0
        return
    }
    $global:LASTEXITCODE = 0
    return
}
if ($joined.StartsWith("build ")) {
    $global:LASTEXITCODE = 0
    return
}
if ($joined.StartsWith("image inspect")) {
    if ($joined.Contains("supplier-risk-hardening.source_sha256")) {
        $fingerprint = "contract-unknown-fingerprint"
        if (Test-Path -LiteralPath $env:HARDENING_RECOVERY_CONTRACT_DOCKER_LOG) {
            foreach ($line in [System.IO.File]::ReadAllLines($env:HARDENING_RECOVERY_CONTRACT_DOCKER_LOG)) {
                if (-not $line.Contains('"build"')) { continue }
                $call = $line | ConvertFrom-Json -AsHashtable
                $callArgs = @($call.arguments)
                for ($index = 0; $index -lt ($callArgs.Count - 1); $index++) {
                    if (($callArgs[$index] -eq "--label") -and ($callArgs[$index + 1] -like "supplier-risk-hardening.source_sha256=*")) {
                        $fingerprint = $callArgs[$index + 1].Substring("supplier-risk-hardening.source_sha256=".Length)
                    }
                }
            }
        }
        if ($mode -eq "image-identity-mismatch") { "contract-mismatched-fingerprint" }
        else { $fingerprint }
        $global:LASTEXITCODE = 0
        return
    }
    elseif ($joined.Contains($ownerKey)) {
        $env:RECOVERY_RUN_ID
        $global:LASTEXITCODE = 0
        return
    }
    else {
        "sha256:contract-recovery-image"
        $global:LASTEXITCODE = 0
        return
    }
}
if ($argList -contains "up") {
    [System.IO.File]::WriteAllText($marker, "owned", [System.Text.UTF8Encoding]::new($false))
    $global:LASTEXITCODE = 0
    return
}
if ($argList -contains "run") {
    if (($argList -contains "alembic") -and $joined.Contains("upgrade head")) {
        if ($mode -eq "migrate-failure") {
            [Console]::Error.WriteLine("contract-forced-migrate-failure")
            $global:LASTEXITCODE = 9
            return
        }
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("tests/seed_e2e.py")) {
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("tests/seed_hardening_e2e.py")) {
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("verify_hardening_restore.py")) {
        $outputIndex = [array]::IndexOf($argList, "--output")
        $modeIndex = [array]::IndexOf($argList, "--mode")
        $outputArg = "/test-evidence/out.json"
        if (($outputIndex -ge 0) -and (($outputIndex + 1) -lt $argList.Count)) {
            $outputArg = [string]$argList[$outputIndex + 1]
        }
        $runMode = "capture"
        if (($modeIndex -ge 0) -and (($modeIndex + 1) -lt $argList.Count)) {
            $runMode = [string]$argList[$modeIndex + 1]
        }
        $hostOutput = Join-Path $env:RECOVERY_EVIDENCE_DIR ([System.IO.Path]::GetFileName($outputArg))
        if ($runMode -eq "capture") {
            $document = [ordered]@{
                schema = "supplier-risk-monitoring/hardening-restore-capture/v1"
                mode = "capture"
                database = "supplier_risk_test"
                migration_version = $env:RECOVERY_ALEMBIC_HEAD
                table_counts = @{}
                row_digests = @{}
                evidence_closure = @{ checked_alert_ids = @(); orphan_alert_ids = @() }
                paused_suppliers = @()
                user_role_status = @{}
            }
        }
        else {
            $passed = ($mode -ne "verify-failure")
            $document = [ordered]@{
                schema = "supplier-risk-monitoring/hardening-restore-verify/v1"
                mode = "verify"
                baseline = @{ path = "/test-evidence/baseline.json"; database = "supplier_risk_test"; migration_version = $env:RECOVERY_ALEMBIC_HEAD }
                restored = @{ migration_version = $env:RECOVERY_ALEMBIC_HEAD }
                comparisons = [ordered]@{
                    migration_version = @{ passed = $passed }
                    table_counts = @{ passed = $passed }
                    row_digests = @{ passed = $passed }
                    evidence_closure = @{ passed = $passed }
                    paused_suppliers = @{ passed = $passed }
                    user_role_status = @{ passed = $passed }
                }
                passed = $passed
            }
        }
        [System.IO.File]::WriteAllText(
            $hostOutput,
            (($document | ConvertTo-Json -Depth 6 -Compress) + [Environment]::NewLine),
            [System.Text.UTF8Encoding]::new($false)
        )
        $global:LASTEXITCODE = 0
        return
    }
    [Console]::Error.WriteLine("contract-unexpected-run: $joined")
    $global:LASTEXITCODE = 91
    return
}
if ($argList -contains "exec") {
    if ($joined.Contains("pg_dump")) {
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("psql") -and $joined.Contains("pg_database")) {
        if ($mode -eq "existing-restore-target") { "1" }
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("createdb")) {
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("pg_restore")) {
        if ($mode -eq "corrupted-backup") {
            [Console]::Error.WriteLine("contract-forced-pg-restore-failure")
            $global:LASTEXITCODE = 41
            return
        }
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined.Contains("dropdb")) {
        $global:LASTEXITCODE = 0
        return
    }
    [Console]::Error.WriteLine("contract-unexpected-exec: $joined")
    $global:LASTEXITCODE = 92
    return
}
if ($argList -contains "cp") {
    $destination = [string]$argList[$argList.Count - 1]
    [System.IO.File]::WriteAllText($destination, "contract-backup-bytes", [System.Text.UTF8Encoding]::new($false))
    $global:LASTEXITCODE = 0
    return
}
if ($argList -contains "down") {
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

    $entryContent = [System.IO.File]::ReadAllText($entryScript, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText(
        $probeEntryScript,
        $entryContent,
        [System.Text.UTF8Encoding]::new($false)
    )

    $env:PATH = "$fakeDockerRoot;$originalPath"
    $env:HARDENING_RECOVERY_CONTRACT_DOCKER_LOG = $dockerCallLog
    $env:HARDENING_RECOVERY_CONTRACT_STACK_MARKER = $stackMarker

    # ------------------------------------------------------------------
    # 1. occupied_project_rejected：项目已有资源 → 非 0 退出且无 build 调用
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "occupied-project"
    $occupied = Invoke-Entry
    Assert-Contract ($occupied.exit_code -ne 0) "占用项目时入口未失败（exit $($occupied.exit_code)）。"
    $occupiedCalls = @(Read-DockerCalls)
    Assert-Contract (@($occupiedCalls | Where-Object { @($_.arguments) -contains "build" }).Count -eq 0) "占用项目时仍构建了镜像。"
    Assert-Contract (@($occupiedCalls | Where-Object { @($_.arguments) -contains "down" }).Count -eq 0) "占用项目时错误清理了他人资源。"
    Assert-Contract ($occupiedCalls.Count -gt 0) "占用项目用例未执行 Docker 资源查询。"
    $result.checks.occupied_project_rejected = "passed"

    # ------------------------------------------------------------------
    # 2. image_identity_mismatch：image inspect 返回错误 label → 中止且无 up
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "image-identity-mismatch"
    $identity = Invoke-Entry
    Assert-Contract ($identity.exit_code -ne 0) "镜像身份校验失败未使入口失败。"
    $identityCalls = @(Read-DockerCalls)
    Assert-Contract (@($identityCalls | Where-Object { @($_.arguments) -contains "up" }).Count -eq 0) "镜像身份校验失败后仍启动了栈。"
    $identityDoc = ConvertFrom-OutputJson -Text $identity.text
    Assert-Contract ($null -ne $identityDoc) "镜像身份校验失败场景未输出 recovery.json。"
    Assert-Contract ($identityDoc.status -eq "failed") "镜像身份校验失败未标记整体 failed。"
    $identityRmCalls = @($identityCalls | Where-Object { (@($_.arguments) -contains "image") -and (@($_.arguments) -contains "rm") })
    Assert-Contract ($identityRmCalls.Count -eq 1) "镜像身份校验失败后未删除本轮唯一 tag（期望 1 次 image rm）。"
    $result.checks.image_identity_mismatch = "passed"

    # ------------------------------------------------------------------
    # 3. existing_restore_target_rejected：psql 预检返回 1 → 非 0 且无 createdb/pg_restore
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "existing-restore-target"
    $existing = Invoke-Entry
    Assert-Contract ($existing.exit_code -ne 0) "已存在恢复目标时入口未失败。"
    $existingCalls = @(Read-DockerCalls)
    Assert-Contract (@($existingCalls | Where-Object { @($_.arguments) -contains "createdb" }).Count -eq 0) "目标已存在仍执行了 createdb。"
    Assert-Contract (@($existingCalls | Where-Object { @($_.arguments) -contains "pg_restore" }).Count -eq 0) "目标已存在仍执行了 pg_restore。"
    Assert-Contract (@($existingCalls | Where-Object { @($_.arguments) -contains "dropdb" }).Count -eq 0) "目标已存在时错误执行了 dropdb（不得 drop/recreate）。"
    $existingDoc = ConvertFrom-OutputJson -Text $existing.text
    Assert-Contract ($null -ne $existingDoc) "目标已存在场景未输出 recovery.json。"
    Assert-Contract ($existingDoc.status -eq "failed") "目标已存在未标记整体 failed。"
    Assert-Contract ($existingDoc.restore.status -eq "failed") "目标已存在未标记 restore 阶段 failed。"
    Assert-Contract ($existingDoc.restore.precheck.existing -eq $true) "目标已存在未记录 existing=true。"
    $result.checks.existing_restore_target_rejected = "passed"

    # ------------------------------------------------------------------
    # 4. corrupted_backup：pg_restore 非 0 → recovery status='failed'
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "corrupted-backup"
    $corrupted = Invoke-Entry
    Assert-Contract ($corrupted.exit_code -eq 41) "pg_restore 失败退出码 41 未被原样传播（实际 $($corrupted.exit_code)）。"
    $corruptedDoc = ConvertFrom-OutputJson -Text $corrupted.text
    Assert-Contract ($null -ne $corruptedDoc) "损坏备份场景未输出 recovery.json。"
    Assert-Contract ($corruptedDoc.status -eq "failed") "损坏备份未标记整体 failed。"
    Assert-Contract ($corruptedDoc.restore.status -eq "failed") "损坏备份未标记 restore 阶段 failed。"
    Assert-Contract ($corruptedDoc.restore.pg_restore.exit_code -eq 41) "损坏备份未记录 pg_restore 原始退出码 41。"
    Assert-Contract ($corruptedDoc.verification.status -eq "not-run") "pg_restore 失败后 verification 未记 not-run。"
    Assert-Contract ($corruptedDoc.verification.reason -eq "preceding-stage-failed") "pg_restore 失败后 verification 缺少 preceding-stage-failed。"
    Assert-Contract ($corruptedDoc.cleanup.dropdb.status -eq "passed") "本轮创建的目标库未在 finally 中 dropdb。"
    $result.checks.corrupted_backup = "passed"

    # ------------------------------------------------------------------
    # 5. rollback_blocked_not_failure：无候选 → rollback blocked 且整体 passed
    # 8. dump_sha256_recorded：dump-sha256.txt 为 64 位 hex（复用正常路径运行）
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "normal"
    $normal = Invoke-Entry
    Assert-Contract ($normal.exit_code -eq 0) "正常路径未成功（exit $($normal.exit_code)）。"
    $normalCalls = @(Read-DockerCalls)
    Assert-Contract (@($normalCalls | Where-Object { @($_.arguments) -contains "app-test" }).Count -eq 0) "正常路径错误启动了 app-test。"
    Assert-Contract (@($normalCalls | Where-Object { @($_.arguments) -contains "pytest" }).Count -eq 0) "正常路径错误执行了 pytest。"
    Assert-Contract (@($normalCalls | Where-Object { @($_.arguments) -contains "pg_dump" }).Count -eq 1) "正常路径应恰好执行一次 pg_dump。"
    Assert-Contract (@($normalCalls | Where-Object { @($_.arguments) -contains "pg_restore" }).Count -eq 1) "正常路径应恰好执行一次 pg_restore。"
    $normalDoc = ConvertFrom-OutputJson -Text $normal.text
    Assert-Contract ($null -ne $normalDoc) "正常路径未输出 recovery.json。"
    Assert-Contract ($normalDoc.status -eq "passed") "正常路径整体未 passed。"
    Assert-Contract ($normalDoc.verification.passed -eq $true) "正常路径 verification.passed 应为 true。"
    Assert-Contract ($normalDoc.rollback_smoke.status -eq "blocked") "无候选镜像时 rollback_smoke 未记 blocked。"
    Assert-Contract ($normalDoc.rollback_smoke.reason -eq "no-previous-verified-candidate-image-available") "rollback_smoke.reason 不正确。"
    Assert-Contract ($null -eq $normalDoc.rollback_smoke.previous_candidate_reference) "rollback_smoke.previous_candidate_reference 应为 null。"
    Assert-Contract ($normalDoc.cleanup.down -eq "passed") "正常路径未执行 owner 校验后的 down。"
    Assert-Contract ($normalDoc.cleanup.image -eq "removed") "正常路径未删除临时镜像。"
    $recoveryPath = Get-RecoveryDocumentPath -Document $normalDoc
    Assert-Contract (Test-Path -LiteralPath $recoveryPath -PathType Leaf) "recovery.json 未写入任务11证据目录。"
    Assert-Contract (Test-NoBom -Path $recoveryPath) "recovery.json 带 BOM。"
    $recoveryFileDoc = Get-Content -Raw -LiteralPath $recoveryPath | ConvertFrom-Json
    Assert-Contract ($recoveryFileDoc.status -eq "passed") "磁盘 recovery.json 与 stdout 不一致。"
    $result.checks.rollback_blocked_not_failure = "passed"

    $runEvidenceDirectory = Split-Path -Parent $recoveryPath
    $dumpShaPath = Join-Path $runEvidenceDirectory "dump-sha256.txt"
    Assert-Contract (Test-Path -LiteralPath $dumpShaPath -PathType Leaf) "dump-sha256.txt 未生成。"
    $dumpShaText = (Get-Content -Raw -LiteralPath $dumpShaPath).Trim()
    Assert-Contract ($dumpShaText -cmatch '^[0-9a-f]{64}$') "dump-sha256.txt 不是 64 位小写 hex（实际：$dumpShaText）。"
    Assert-Contract ($dumpShaText -ceq [string]$normalDoc.dump.sha256) "dump-sha256.txt 与 recovery.json 记录不一致。"
    $result.checks.dump_sha256_recorded = "passed"

    # ------------------------------------------------------------------
    # 6. cleanup_owner_only：存在外来资源 → 拒绝 down
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "cleanup-owner-only"
    $ownerOnly = Invoke-Entry
    Assert-Contract ($ownerOnly.exit_code -ne 0) "存在外来资源时清理未被拒绝。"
    $ownerOnlyCalls = @(Read-DockerCalls)
    Assert-Contract (@($ownerOnlyCalls | Where-Object { @($_.arguments) -contains "down" }).Count -eq 0) "存在外来资源时仍执行了 down。"
    $ownerOnlyDoc = ConvertFrom-OutputJson -Text $ownerOnly.text
    Assert-Contract ($null -ne $ownerOnlyDoc) "外来资源场景未输出 recovery.json。"
    Assert-Contract ($ownerOnlyDoc.status -eq "failed") "外来资源被拒绝清理却未标记 failed。"
    Assert-Contract ($ownerOnlyDoc.cleanup.down -eq "failed") "cleanup.down 未标记 failed。"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace([string]$ownerOnlyDoc.cleanup.error)) "cleanup.error 缺少拒绝原因。"
    $result.checks.cleanup_owner_only = "passed"

    # ------------------------------------------------------------------
    # 7. verify_failure_surfaced：verify 输出 passed=false → status='failed' 不伪造
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "verify-failure"
    $verifyFailure = Invoke-Entry
    Assert-Contract ($verifyFailure.exit_code -ne 0) "verify passed=false 未使入口失败。"
    $verifyFailureDoc = ConvertFrom-OutputJson -Text $verifyFailure.text
    Assert-Contract ($null -ne $verifyFailureDoc) "verify 失败场景未输出 recovery.json。"
    Assert-Contract ($verifyFailureDoc.status -eq "failed") "verify passed=false 被伪造成通过。"
    Assert-Contract ($verifyFailureDoc.verification.status -eq "failed") "verify 阶段未标记 failed。"
    Assert-Contract ($verifyFailureDoc.verification.passed -eq $false) "verify 结果未保留 passed=false。"
    Assert-Contract ($verifyFailureDoc.verification.compare.passed -eq $false) "compare.passed 未保留 false。"
    $result.checks.verify_failure_surfaced = "passed"

    # ------------------------------------------------------------------
    # 9. fail_fast：migrate 失败 → 后续阶段 not-run
    # ------------------------------------------------------------------
    Set-ContractMode -Mode "migrate-failure"
    $failFast = Invoke-Entry
    Assert-Contract ($failFast.exit_code -eq 9) "migrate 失败退出码 9 未被传播（实际 $($failFast.exit_code)）。"
    $failFastCalls = @(Read-DockerCalls)
    Assert-Contract (@($failFastCalls | Where-Object { @($_.arguments) -contains "up" }).Count -eq 1) "migrate 失败前应恰好启动一次栈。"
    Assert-Contract (@($failFastCalls | Where-Object { @($_.arguments) -contains "pg_dump" }).Count -eq 0) "migrate 失败后仍执行 pg_dump。"
    Assert-Contract (@($failFastCalls | Where-Object { @($_.arguments) -contains "createdb" }).Count -eq 0) "migrate 失败后仍执行 createdb。"
    Assert-Contract (@($failFastCalls | Where-Object { @($_.arguments) -contains "pg_restore" }).Count -eq 0) "migrate 失败后仍执行 pg_restore。"
    $failFastSeedCalls = @($failFastCalls | Where-Object { (@($_.arguments) -contains "python") -and (@($_.arguments) -contains "tests/seed_e2e.py") })
    Assert-Contract ($failFastSeedCalls.Count -eq 0) "migrate 失败后仍执行 seed。"
    $failFastDoc = ConvertFrom-OutputJson -Text $failFast.text
    Assert-Contract ($null -ne $failFastDoc) "migrate 失败场景未输出 recovery.json。"
    Assert-Contract ($failFastDoc.status -eq "failed") "migrate 失败未标记整体 failed。"
    Assert-Contract ($failFastDoc.migrate.status -eq "failed") "migrate 阶段未标记 failed。"
    Assert-Contract ($failFastDoc.migrate.exit_code -eq 9) "migrate 阶段未记录原始退出码 9。"
    foreach ($stageName in @("seed", "dump", "restore", "verification", "rollback_smoke")) {
        $stage = $failFastDoc.$stageName
        Assert-Contract ($stage.status -eq "not-run") "migrate 失败后 $stageName 未记 not-run。"
        Assert-Contract ($stage.reason -eq "preceding-stage-failed") "migrate 失败后 $stageName 未记 preceding-stage-failed（实际 $($stage.reason)）。"
    }
    $result.checks.fail_fast = "passed"
}
catch {
    $contractFailed = $true
    $result.error = $_.Exception.Message
    [Console]::Error.WriteLine($_.Exception.Message)
}
finally {
    $env:PATH = $originalPath
    $env:HARDENING_RECOVERY_CONTRACT_MODE = $originalMode
    $env:HARDENING_RECOVERY_CONTRACT_DOCKER_LOG = $originalLog
    $env:HARDENING_RECOVERY_CONTRACT_STACK_MARKER = $originalMarker
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $probeEntryScript -Force -ErrorAction SilentlyContinue
    $result.checks_passed = @($result.checks.Keys | Where-Object { $result.checks[$_] -eq "passed" }).Count
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

exit $(if ($contractFailed) { 1 } else { 0 })
