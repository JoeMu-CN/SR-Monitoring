$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeArgs = @("compose", "run", "--rm", "--build", "app", "pytest") + $args

Push-Location $repoRoot
try {
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
