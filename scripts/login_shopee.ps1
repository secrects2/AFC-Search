param(
    [string]$Url = "https://shopee.tw/"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SetupScript = Join-Path $ProjectRoot "tools\setup_shopee_profile.py"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python virtual environment not found: $PythonExe"
}

if (-not (Test-Path -LiteralPath $SetupScript)) {
    throw "Shopee profile setup script not found: $SetupScript"
}

& $PythonExe $SetupScript --url $Url
exit $LASTEXITCODE
