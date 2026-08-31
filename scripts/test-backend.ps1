param(
    [string]$DatabaseUrl = "postgresql+psycopg://supplier_risk_test:test_only_password@postgres-test:5432/supplier_risk_test",
    [string[]]$PytestArgs = @(
        "tests/test_database_guard.py",
        "tests/test_e2e_seed.py",
        "tests/test_health.py"
    )
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "compose.test.yaml"
$projectName = "supplier-risk-frontend-gap-test"
$allowedDatabaseName = "supplier_risk_test"

try {
    $databaseName = ([Uri]$DatabaseUrl).AbsolutePath.TrimStart("/")
}
catch {
    [Console]::Error.WriteLine("拒绝运行数据库测试：DATABASE_URL 无法解析。")
    exit 2
}
if (-not $databaseName.EndsWith("_test") -or $databaseName -ne $allowedDatabaseName) {
    [Console]::Error.WriteLine(
        "拒绝运行数据库测试：仅允许显式数据库 supplier_risk_test；当前数据库为 $databaseName。"
    )
    exit 2
}

$env:TEST_DATABASE_URL = $DatabaseUrl
$compose = @("compose", "--project-name", $projectName, "--file", $composeFile)
$exitCode = 1

Push-Location $repoRoot
try {
    & docker @compose up --detach --wait app-test
    if ($LASTEXITCODE -ne 0) { throw "隔离测试栈启动失败。" }

    & docker @compose --profile tools run --rm test-runner pytest @PytestArgs
    if ($LASTEXITCODE -ne 0) { throw "pytest 失败。" }

    $health = Invoke-RestMethod -Uri "http://127.0.0.1:18080/api/v1/system/health"
    if ($health.status -ne "ok" -or $health.database -ne "ok") {
        throw "健康检查未返回数据库可用状态。"
    }
    Write-Output '{"database":"supplier_risk_test","pytest":"passed","health":"ok"}'
    $exitCode = 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
}
finally {
    & docker @compose --profile tools down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("隔离测试栈清理失败。")
        $exitCode = 1
    }
    Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
    Pop-Location
}

exit $exitCode
