[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 任务11：隔离备份/恢复演练主编排。
# - 只新增本文件、README 规范中列出的 compose overlay、独立校验器与契约脚本。
# - 不发布任何宿主端口；只启动 postgres-test 与 test-runner（profile tools）；
#   绝不启动 app-test；绝不对恢复库运行 pytest。
# - 首败即终止后续阶段并记 not-run + preceding-stage-failed；
#   exit 传播首个失败的退出码。
# ---------------------------------------------------------------------------

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeBaseFile = Join-Path $repoRoot "compose.test.yaml"
$composeOverrideFile = Join-Path $repoRoot "compose.hardening-recovery-test.yaml"
$dockerfile = Join-Path $repoRoot "Dockerfile"
$alembicVersionsDirectory = Join-Path $repoRoot "backend/alembic/versions"

$projectName = "supplier-risk-hardening-recovery-test"
$ownerKey = "supplier-risk-hardening-recovery.owner"
$sourceDatabase = "supplier_risk_test"
$restoreDatabase = "supplier_risk_restore_test"
$sourceDatabaseUrl = "postgresql+psycopg://supplier_risk_test:test_only_password@postgres-test:5432/$sourceDatabase"
$restoreDatabaseUrl = "postgresql+psycopg://supplier_risk_test:test_only_password@postgres-test:5432/$restoreDatabase"
$rollbackBlockedReason = "no-previous-verified-candidate-image-available"

$runId = "{0}-{1}" -f ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), [Guid]::NewGuid().ToString("N").Substring(0, 8)
$imageReference = "supplierriskmonitoring-hardening-recovery-test:$runId"

$evidenceRoot = Join-Path $repoRoot ".omo/evidence/task-11-current-version-hardening"
$evidenceDirectory = Join-Path $evidenceRoot $runId
$recoveryPath = Join-Path $evidenceDirectory "recovery.json"
$buildLogPath = Join-Path $evidenceDirectory "build.txt"
$upLogPath = Join-Path $evidenceDirectory "compose-up.txt"
$migrateLogPath = Join-Path $evidenceDirectory "migrate.txt"
$seedE2eLogPath = Join-Path $evidenceDirectory "seed-e2e.txt"
$seedHardeningLogPath = Join-Path $evidenceDirectory "seed-hardening.txt"
$captureLogPath = Join-Path $evidenceDirectory "capture.txt"
$dumpLogPath = Join-Path $evidenceDirectory "dump.txt"
$restorePrecheckLogPath = Join-Path $evidenceDirectory "restore-precheck.txt"
$restoreLogPath = Join-Path $evidenceDirectory "restore.txt"
$verifyLogPath = Join-Path $evidenceDirectory "verify-console.txt"
$rollbackLogPath = Join-Path $evidenceDirectory "rollback.txt"
$dumpShaPath = Join-Path $evidenceDirectory "dump-sha256.txt"
$cleanupLogPath = Join-Path $evidenceDirectory "cleanup.txt"

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
    $ownerLabel = "label=$ownerKey=$runId"
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
        Stop-WithValidationError "拒绝运行：Compose 项目 $projectName 已有资源；不会复用、覆盖或清理。"
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

function Get-AlembicHead {
    <#
        只读源码推导唯一 head：head = 出现在 revision 中、但从未出现在任何
        down_revision 中的修订；down_revision 支持字符串与元组（合并迁移）。
    #>
    if (-not (Test-Path -LiteralPath $alembicVersionsDirectory -PathType Container)) {
        throw "缺少 Alembic 版本目录：$alembicVersionsDirectory"
    }
    $revisionPattern = '(?m)^revision\s*(?::[^=]+)?=\s*["'']([^"'']+)["'']'
    $downRevisionPattern = '(?ms)^down_revision\s*(?::[^=]+)?=\s*(?<value>.+?)(?=^\w|\Z)'
    $revisions = [System.Collections.Generic.HashSet[string]]::new()
    $children = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($file in @(Get-ChildItem -LiteralPath $alembicVersionsDirectory -Filter "*.py" -File)) {
        $content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.UTF8Encoding]::new($false))
        $revisionMatch = [regex]::Match($content, $revisionPattern)
        if (-not $revisionMatch.Success) {
            throw "无法解析 Alembic revision：$($file.Name)"
        }
        $revisions.Add($revisionMatch.Groups[1].Value) | Out-Null
        $downMatch = [regex]::Match($content, $downRevisionPattern)
        if ($downMatch.Success) {
            foreach ($quoted in [regex]::Matches($downMatch.Groups["value"].Value, '["'']([^"'']+)["'']')) {
                $children.Add($quoted.Groups[1].Value) | Out-Null
            }
        }
    }
    $heads = @($revisions | Where-Object { -not $children.Contains($_) })
    if ($heads.Count -ne 1) {
        throw "Alembic head 不唯一或缺失：$($heads -join ', ')"
    }
    return [string]$heads[0]
}

function Invoke-RecoveryCommand {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][scriptblock]$Command
    )

    & $Command *> $LogPath
    return $LASTEXITCODE
}

function New-NativeFailure {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][int]$ExitCode,
        [Parameter(Mandatory)][string]$LogPath
    )

    return [System.ComponentModel.Win32Exception]::new($ExitCode, "$Name 失败（exit $ExitCode）；详见 $LogPath。")
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

    & docker @compose down --volumes --remove-orphans *>> $cleanupLogPath
    if ($LASTEXITCODE -ne 0) { throw "本轮 Compose 资源清理失败（exit $LASTEXITCODE）。" }
    $residual = Get-ProjectResources
    if ((Get-ResourceCount -Resources $residual) -ne 0) {
        throw "本轮 Compose 资源清理后仍有残留。"
    }
}

# ---------------------------------------------------------------------------
# 预检：必需路径 + 拒绝已有项目资源（在创建任何资源/构建之前）
# ---------------------------------------------------------------------------
foreach ($requiredPath in @($composeBaseFile, $composeOverrideFile, $dockerfile, $alembicVersionsDirectory)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        Stop-WithValidationError "缺少恢复演练所需路径：$requiredPath"
    }
}
Assert-NoProjectResources

New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null
$sourceFingerprint = Get-SourceFingerprint

$previousEnvironment = @{}
foreach ($name in @("RECOVERY_RUN_ID", "RECOVERY_TEST_IMAGE", "RECOVERY_EVIDENCE_DIR", "RECOVERY_ALEMBIC_HEAD")) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$env:RECOVERY_RUN_ID = $runId
$env:RECOVERY_TEST_IMAGE = $imageReference
$env:RECOVERY_EVIDENCE_DIR = $evidenceDirectory

$startedAt = [DateTimeOffset]::UtcNow
$imageBuilt = $false
$postgresStarted = $false
$restoreTargetCreated = $false
$exitCode = 1
$errorMessage = $null
$alembicHead = $null
$migrate = $null
$seed = $null
$dump = $null
$restoreStage = $null
$verification = $null
$rollbackSmoke = $null
$imageRecord = [ordered]@{
    reference = $imageReference
    id = $null
    source_label = $null
    owner_label = $null
}

try {
    # ---------- 3. 构建镜像并校验身份 ----------
    $buildExit = Invoke-RecoveryCommand -Name "镜像构建" -LogPath $buildLogPath -Command {
        & docker build --tag $imageReference `
            --label "supplier-risk-hardening.source_sha256=$sourceFingerprint" `
            --label "supplier-risk-hardening-recovery.owner=$runId" `
            --file $dockerfile $repoRoot
    }
    if ($buildExit -ne 0) {
        throw (New-NativeFailure -Name "镜像构建" -ExitCode $buildExit -LogPath $buildLogPath)
    }
    $imageBuilt = $true

    $imageId = ((@(& docker image inspect --format '{{.Id}}' $imageReference)) -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($imageId)) {
        throw "无法读取本轮构建镜像 ID。"
    }
    $imageRecord.id = $imageId
    $imageRecord.source_label = ((@(& docker image inspect --format '{{ index .Config.Labels "supplier-risk-hardening.source_sha256" }}' $imageReference)) -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or $imageRecord.source_label -cne $sourceFingerprint) {
        throw "本轮镜像源指纹与当前工作区不一致。"
    }
    $imageRecord.owner_label = ((@(& docker image inspect --format '{{ index .Config.Labels "supplier-risk-hardening-recovery.owner" }}' $imageReference)) -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or $imageRecord.owner_label -cne $runId) {
        throw "本轮镜像缺少正确 owner label。"
    }

    # ---------- 4. 只启动 postgres-test（不发布宿主端口） ----------
    $upExit = Invoke-RecoveryCommand -Name "postgres-test 启动" -LogPath $upLogPath -Command {
        & docker @compose up --detach --wait postgres-test
    }
    if ($upExit -ne 0) {
        throw (New-NativeFailure -Name "postgres-test 启动" -ExitCode $upExit -LogPath $upLogPath)
    }
    $postgresStarted = $true

    # ---------- 5. 迁移 ----------
    $migrateExit = Invoke-RecoveryCommand -Name "alembic upgrade head" -LogPath $migrateLogPath -Command {
        & docker @compose --profile tools run --rm --no-deps test-runner alembic upgrade head
    }
    $migrate = [ordered]@{
        status = $(if ($migrateExit -eq 0) { "passed" } else { "failed" })
        reason = $null
        exit_code = $migrateExit
        log = $migrateLogPath
        error = $null
    }
    if ($migrateExit -ne 0) {
        $migrate.error = "alembic upgrade head 失败（exit $migrateExit）。"
        throw (New-NativeFailure -Name "alembic upgrade head" -ExitCode $migrateExit -LogPath $migrateLogPath)
    }

    # ---------- 6. seed_e2e ----------
    $seedE2eExit = Invoke-RecoveryCommand -Name "seed_e2e" -LogPath $seedE2eLogPath -Command {
        & docker @compose --profile tools run --rm --no-deps test-runner python tests/seed_e2e.py
    }
    if ($seedE2eExit -ne 0) {
        $seed = [ordered]@{
            status = "failed"
            reason = $null
            error = "python tests/seed_e2e.py 失败（exit $seedE2eExit）。"
            e2e = [ordered]@{ status = "failed"; reason = $null; exit_code = $seedE2eExit; log = $seedE2eLogPath }
            hardening = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null; log = $seedHardeningLogPath }
        }
        throw (New-NativeFailure -Name "seed_e2e" -ExitCode $seedE2eExit -LogPath $seedE2eLogPath)
    }

    # ---------- 7. seed_hardening_e2e ----------
    $seedHardeningExit = Invoke-RecoveryCommand -Name "seed_hardening_e2e" -LogPath $seedHardeningLogPath -Command {
        & docker @compose --profile tools run --rm --no-deps test-runner python tests/seed_hardening_e2e.py
    }
    $seed = [ordered]@{
        status = $(if ($seedHardeningExit -eq 0) { "passed" } else { "failed" })
        reason = $null
        error = $null
        e2e = [ordered]@{ status = "passed"; reason = $null; exit_code = $seedE2eExit; log = $seedE2eLogPath }
        hardening = [ordered]@{
            status = $(if ($seedHardeningExit -eq 0) { "passed" } else { "failed" })
            reason = $null
            exit_code = $seedHardeningExit
            log = $seedHardeningLogPath
        }
    }
    if ($seedHardeningExit -ne 0) {
        $seed.error = "python tests/seed_hardening_e2e.py 失败（exit $seedHardeningExit）。"
        throw (New-NativeFailure -Name "seed_hardening_e2e" -ExitCode $seedHardeningExit -LogPath $seedHardeningLogPath)
    }

    # ---------- 8. 源码推导 Alembic head ----------
    $alembicHead = Get-AlembicHead
    $env:RECOVERY_ALEMBIC_HEAD = $alembicHead

    # ---------- 9. capture 基线 ----------
    $captureExit = Invoke-RecoveryCommand -Name "capture 基线" -LogPath $captureLogPath -Command {
        & docker @compose --profile tools run --rm --no-deps test-runner python tests/verify_hardening_restore.py --mode capture --database-url $sourceDatabaseUrl --output /test-evidence/baseline.json
    }
    $verification = [ordered]@{
        status = "not-run"
        reason = $null
        passed = $null
        error = $null
        capture = [ordered]@{
            status = $(if ($captureExit -eq 0) { "passed" } else { "failed" })
            reason = $null
            exit_code = $captureExit
            output = "baseline.json"
            migration_version = $null
            expected_migration_version = $null
            error = $null
        }
        compare = [ordered]@{
            status = "not-run"
            reason = $null
            exit_code = $null
            output = "verify.json"
            passed = $null
            failed_comparisons = @()
            error = $null
        }
    }
    if ($captureExit -ne 0) {
        $verification.status = "failed"
        $verification.capture.error = "capture 失败（exit $captureExit）。"
        $verification.error = "capture 失败（exit $captureExit）；详见 $captureLogPath。"
        throw (New-NativeFailure -Name "capture 基线" -ExitCode $captureExit -LogPath $captureLogPath)
    }
    $baselinePath = Join-Path $evidenceDirectory "baseline.json"
    if (-not (Test-Path -LiteralPath $baselinePath -PathType Leaf)) {
        throw "capture 未生成基线文件：$baselinePath"
    }
    $baselineDocument = Get-Content -Raw -LiteralPath $baselinePath | ConvertFrom-Json
    $baselineMigration = [string]$baselineDocument.migration_version
    $verification.capture.migration_version = $baselineMigration
    $verification.capture.expected_migration_version = $alembicHead
    if ($baselineMigration -cne $alembicHead) {
        $verification.status = "failed"
        $verification.capture.error = "capture migration_version 与源码 head 不一致：$baselineMigration != $alembicHead。"
        $verification.error = "capture migration_version 与源码 head 不一致。"
        throw "capture migration_version 与源码 head 不一致：$baselineMigration != $alembicHead。"
    }

    # ---------- 10. pg_dump ----------
    $dumpExit = Invoke-RecoveryCommand -Name "pg_dump" -LogPath $dumpLogPath -Command {
        & docker @compose exec -T postgres-test pg_dump -U supplier_risk_test -d supplier_risk_test -Fc -f "/tmp/$runId.dump"
    }
    $dump = [ordered]@{
        status = $(if ($dumpExit -eq 0) { "passed" } else { "failed" })
        reason = $null
        exit_code = $dumpExit
        container_path = "/tmp/$runId.dump"
        host_path = $null
        sha256 = $null
        sha256_file = "dump-sha256.txt"
        log = $dumpLogPath
        error = $null
    }
    if ($dumpExit -ne 0) {
        $dump.error = "pg_dump 失败（exit $dumpExit）。"
        throw (New-NativeFailure -Name "pg_dump" -ExitCode $dumpExit -LogPath $dumpLogPath)
    }

    # ---------- 11. 备份复制到宿主并记录 SHA-256 ----------
    $hostDumpPath = Join-Path $evidenceDirectory "backup.dump"
    & docker @compose cp "postgres-test:/tmp/$runId.dump" $hostDumpPath *>> $dumpLogPath
    $dumpCopyExit = $LASTEXITCODE
    if ($dumpCopyExit -ne 0) {
        $dump.error = "docker compose cp 备份文件失败（exit $dumpCopyExit）。"
        throw (New-NativeFailure -Name "备份复制" -ExitCode $dumpCopyExit -LogPath $dumpLogPath)
    }
    if (-not (Test-Path -LiteralPath $hostDumpPath -PathType Leaf)) {
        throw "备份文件未落到宿主证据目录：$hostDumpPath"
    }
    $dumpHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $hostDumpPath).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText($dumpShaPath, "$dumpHash$([Environment]::NewLine)", [System.Text.UTF8Encoding]::new($false))
    $dump.host_path = $hostDumpPath
    $dump.sha256 = $dumpHash

    # ---------- 12. 恢复目标存在性预检（不 createdb/pg_restore，不 drop/recreate） ----------
    $precheckOutput = @(& docker @compose exec -T postgres-test psql -U supplier_risk_test -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='supplier_risk_restore_test'" 2>&1)
    $precheckExit = $LASTEXITCODE
    $precheckText = (($precheckOutput | ForEach-Object { $_.ToString() }) -join "").Trim()
    [System.IO.File]::WriteAllText(
        $restorePrecheckLogPath,
        "exit=$precheckExit$([Environment]::NewLine)output=$precheckText$([Environment]::NewLine)",
        [System.Text.UTF8Encoding]::new($false)
    )
    $restoreStage = [ordered]@{
        status = "not-run"
        reason = $null
        target = $restoreDatabase
        target_created_by_run = $false
        precheck = [ordered]@{
            status = "not-run"
            reason = $null
            exit_code = $precheckExit
            existing = $null
            log = $restorePrecheckLogPath
        }
        createdb = [ordered]@{ status = "not-run"; reason = $null; exit_code = $null }
        pg_restore = [ordered]@{ status = "not-run"; reason = $null; exit_code = $null }
        log = $restoreLogPath
        error = $null
    }
    if ($precheckExit -ne 0) {
        $restoreStage.status = "failed"
        $restoreStage.precheck.status = "failed"
        $restoreStage.error = "恢复目标存在性预检失败（exit $precheckExit）。"
        throw (New-NativeFailure -Name "恢复目标预检" -ExitCode $precheckExit -LogPath $restorePrecheckLogPath)
    }
    $restoreStage.precheck.status = "passed"
    if ($precheckText -ne "") {
        $restoreStage.status = "failed"
        $restoreStage.precheck.existing = $true
        $restoreStage.error = "恢复目标 $restoreDatabase 已存在；本轮不 createdb/pg_restore，不 drop/recreate。"
        throw "拒绝恢复：目标库 $restoreDatabase 已存在，本轮不创建也不覆盖。"
    }
    $restoreStage.precheck.existing = $false

    # ---------- 13. createdb ----------
    & docker @compose exec -T postgres-test createdb -U supplier_risk_test -O supplier_risk_test $restoreDatabase *> $restoreLogPath
    $createdbExit = $LASTEXITCODE
    $restoreStage.createdb.exit_code = $createdbExit
    if ($createdbExit -ne 0) {
        $restoreStage.status = "failed"
        $restoreStage.createdb.status = "failed"
        $restoreStage.error = "createdb 失败（exit $createdbExit）。"
        throw (New-NativeFailure -Name "createdb" -ExitCode $createdbExit -LogPath $restoreLogPath)
    }
    $restoreStage.createdb.status = "passed"
    $restoreTargetCreated = $true
    $restoreStage.target_created_by_run = $true

    # ---------- 14. pg_restore ----------
    & docker @compose exec -T postgres-test pg_restore -U supplier_risk_test -d $restoreDatabase --no-owner "/tmp/$runId.dump" *>> $restoreLogPath
    $pgRestoreExit = $LASTEXITCODE
    $restoreStage.pg_restore.exit_code = $pgRestoreExit
    if ($pgRestoreExit -ne 0) {
        $restoreStage.status = "failed"
        $restoreStage.pg_restore.status = "failed"
        $restoreStage.error = "pg_restore 失败（exit $pgRestoreExit）。"
        throw (New-NativeFailure -Name "pg_restore" -ExitCode $pgRestoreExit -LogPath $restoreLogPath)
    }
    $restoreStage.pg_restore.status = "passed"
    $restoreStage.status = "passed"

    # ---------- 15. verify 恢复库 ----------
    $verifyExit = Invoke-RecoveryCommand -Name "verify 恢复库" -LogPath $verifyLogPath -Command {
        & docker @compose --profile tools run --rm --no-deps test-runner python tests/verify_hardening_restore.py --mode verify --database-url $restoreDatabaseUrl --baseline /test-evidence/baseline.json --output /test-evidence/verify.json
    }
    $verification.compare.exit_code = $verifyExit
    if ($verifyExit -ne 0) {
        $verification.status = "failed"
        $verification.compare.status = "failed"
        $verification.compare.error = "verify 命令失败（exit $verifyExit）。"
        $verification.error = "verify 命令失败（exit $verifyExit）；详见 $verifyLogPath。"
        throw (New-NativeFailure -Name "verify 恢复库" -ExitCode $verifyExit -LogPath $verifyLogPath)
    }
    $verifyPath = Join-Path $evidenceDirectory "verify.json"
    if (-not (Test-Path -LiteralPath $verifyPath -PathType Leaf)) {
        throw "verify 未生成结果文件：$verifyPath"
    }
    $verifyDocument = Get-Content -Raw -LiteralPath $verifyPath | ConvertFrom-Json
    $verifyPassed = [bool]$verifyDocument.passed
    $verification.compare.passed = $verifyPassed
    $verification.passed = $verifyPassed
    if (-not $verifyPassed) {
        $verification.status = "failed"
        $verification.compare.status = "failed"
        $failedComparisons = @()
        foreach ($property in $verifyDocument.comparisons.psobject.Properties) {
            if (-not [bool]$property.Value.passed) {
                $failedComparisons += $property.Name
            }
        }
        $verification.compare.failed_comparisons = $failedComparisons
        $verification.error = "恢复库校验未通过（verify.json passed=false；失败子项：$($failedComparisons -join ', ')）。"
        throw "恢复库校验未通过：verify.json passed=false。"
    }
    $verification.compare.status = "passed"
    $verification.status = "passed"

    # ---------- 16. rollback_smoke：无上一已验证候选镜像，固定记 blocked ----------
    $rollbackSmoke = [ordered]@{
        status = "blocked"
        reason = $rollbackBlockedReason
        previous_candidate_reference = $null
    }
    [System.IO.File]::WriteAllText(
        $rollbackLogPath,
        (($rollbackSmoke | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )

    $exitCode = 0
}
catch {
    $errorMessage = $_.Exception.Message
    if ($_.Exception -is [System.ComponentModel.Win32Exception]) {
        $exitCode = $_.Exception.NativeErrorCode
    }
    else {
        $exitCode = 1
    }
    [Console]::Error.WriteLine($errorMessage)
}
finally {
    # ---------- 17. 兜底清理 ----------
    $cleanup = [ordered]@{
        attempted = $true
        passed = $false
        reason = $null
        dropdb = [ordered]@{ status = "not-run"; exit_code = $null; reason = $null; error = $null }
        down = "not-run"
        image = "not-run"
        log = $cleanupLogPath
        error = $null
    }

    if ($restoreTargetCreated) {
        try {
            & docker @compose exec -T postgres-test dropdb -U supplier_risk_test --if-exists $restoreDatabase *>> $cleanupLogPath
            $dropdbExit = $LASTEXITCODE
            $cleanup.dropdb.exit_code = $dropdbExit
            if ($dropdbExit -eq 0) { $cleanup.dropdb.status = "passed" }
            else { $cleanup.dropdb.status = "failed-non-fatal" }
        }
        catch {
            $cleanup.dropdb.status = "failed-non-fatal"
            $cleanup.dropdb.error = $_.Exception.Message
        }
    }
    else {
        $cleanup.dropdb.reason = "restore-target-not-created-by-this-run"
    }

    try {
        Stop-OwnedStack
        $cleanup.down = "passed"
    }
    catch {
        $cleanup.down = "failed"
        $cleanup.error = $_.Exception.Message
        if ($exitCode -eq 0) { $exitCode = 1 }
    }

    if ($imageBuilt) {
        & docker image rm $imageReference *>> $cleanupLogPath
        $imageRmExit = $LASTEXITCODE
        if ($imageRmExit -eq 0) {
            $cleanup.image = "removed"
        }
        else {
            $cleanup.image = "failed"
            $cleanup.error = "临时镜像删除失败（exit $imageRmExit）。"
            if ($exitCode -eq 0) { $exitCode = 1 }
        }
    }
    else {
        $cleanup.image = "not-built"
    }
    $cleanup.passed = ($cleanup.down -eq "passed") -and ($cleanup.image -ne "failed") -and ($null -eq $cleanup.error)

    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }

    # 未经历阶段统一记 not-run + preceding-stage-failed。
    if ($null -eq $migrate) {
        $migrate = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null; log = $migrateLogPath; error = $null }
    }
    if ($null -eq $seed) {
        $seed = [ordered]@{
            status = "not-run"
            reason = "preceding-stage-failed"
            error = $null
            e2e = [ordered]@{ status = "not-run"; exit_code = $null; log = $seedE2eLogPath }
            hardening = [ordered]@{ status = "not-run"; exit_code = $null; log = $seedHardeningLogPath }
        }
    }
    if ($null -eq $dump) {
        $dump = [ordered]@{
            status = "not-run"
            reason = "preceding-stage-failed"
            exit_code = $null
            container_path = "/tmp/$runId.dump"
            host_path = $null
            sha256 = $null
            sha256_file = "dump-sha256.txt"
            log = $dumpLogPath
            error = $null
        }
    }
    if ($null -eq $restoreStage) {
        $restoreStage = [ordered]@{
            status = "not-run"
            reason = "preceding-stage-failed"
            target = $restoreDatabase
            target_created_by_run = $false
            precheck = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null; existing = $null; log = $restorePrecheckLogPath }
            createdb = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null }
            pg_restore = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null }
            log = $restoreLogPath
            error = $null
        }
    }
    if ($null -eq $verification) {
        $verification = [ordered]@{
            status = "not-run"
            reason = "preceding-stage-failed"
            passed = $null
            error = $null
            capture = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null; output = "baseline.json"; migration_version = $null; expected_migration_version = $null; error = $null }
            compare = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; exit_code = $null; output = "verify.json"; passed = $null; failed_comparisons = @(); error = $null }
        }
    }
    if ($null -eq $rollbackSmoke) {
        if (($exitCode -eq 0) -and ($null -eq $errorMessage)) {
            $rollbackSmoke = [ordered]@{ status = "blocked"; reason = $rollbackBlockedReason; previous_candidate_reference = $null }
        }
        else {
            $rollbackSmoke = [ordered]@{ status = "not-run"; reason = "preceding-stage-failed"; previous_candidate_reference = $null }
        }
        [System.IO.File]::WriteAllText(
            $rollbackLogPath,
            (($rollbackSmoke | ConvertTo-Json -Compress) + [Environment]::NewLine),
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    # 被前序失败截断的已创建阶段：状态保持 not-run 时统一回填 reason。
    foreach ($stage in @($migrate, $seed, $dump, $restoreStage, $verification)) {
        if (($null -ne $stage) -and ($stage["status"] -eq "not-run") -and ($null -eq $stage["reason"])) {
            $stage["reason"] = "preceding-stage-failed"
        }
    }

    $recovery = [ordered]@{
        schema = "supplier-risk-monitoring/hardening-recovery-run/v1"
        status = $(if ($exitCode -eq 0) { "passed" } else { "failed" })
        run_id = $runId
        project = $projectName
        database = [ordered]@{ source = $sourceDatabase; restore = $restoreDatabase }
        image = $imageRecord
        source_sha256 = $sourceFingerprint
        migrate = $migrate
        seed = $seed
        dump = $dump
        restore = $restoreStage
        verification = $verification
        rollback_smoke = $rollbackSmoke
        cleanup = $cleanup
        started_at_utc = $startedAt.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        evidence_directory = $evidenceDirectory
        error = $errorMessage
    }
    $recoveryJson = $recovery | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText(
        $recoveryPath,
        "$recoveryJson$([Environment]::NewLine)",
        [System.Text.UTF8Encoding]::new($false)
    )
    [Console]::Out.WriteLine($recoveryJson)
}

exit $exitCode
