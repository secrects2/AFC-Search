param(
    [string]$Url = "https://shopee.tw/"
)

$ErrorActionPreference = "Stop"
$StartScript = Join-Path $PSScriptRoot "start_shopee_chrome.ps1"

if (-not (Test-Path -LiteralPath $StartScript)) {
    throw "Shopee Chrome CDP startup script not found: $StartScript"
}

& $StartScript -Url $Url
exit $LASTEXITCODE
