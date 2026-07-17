<#
.SYNOPSIS
    Start the user's installed Google Chrome with a local CDP endpoint.
.DESCRIPTION
    The AFC Shopee providers attach to this visible Chrome session so the
    user's normal profile, bookmarks, and Shopee login can be reused.
    This script never prints or reads browser credentials.
##>
param(
    [int]$Port = 9223,
    [string]$Url = "https://shopee.tw/"
)

$ErrorActionPreference = "Stop"

function Get-ChromeExecutable {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw "Google Chrome executable not found."
}

function Test-CdpEndpoint {
    param([string]$Endpoint)
    try {
        $response = Invoke-WebRequest -Uri $Endpoint -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

$endpoint = "http://127.0.0.1:$Port/json/version"
if (Test-CdpEndpoint $endpoint) {
    Write-Host "Chrome CDP is already available: http://127.0.0.1:$Port"
    exit 0
}

$chromeProcess = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "Google\\Chrome\\User Data" -and
        $_.CommandLine -notmatch "--type="
    } |
    Select-Object -First 1
if ($chromeProcess) {
    throw "Chrome is already running without CDP. Close all Chrome windows, then run this script again. No process was terminated."
}

$chromeExe = Get-ChromeExecutable
$userDataDir = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
if (-not (Test-Path -LiteralPath $userDataDir)) {
    throw "Chrome user data directory not found: $userDataDir"
}

$arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$userDataDir",
    "--profile-directory=Default",
    "--restore-last-session",
    "--no-first-run",
    "--no-default-browser-check",
    "--lang=zh-TW",
    $Url
)

Write-Host "Starting installed Google Chrome with CDP port $Port..."
Start-Process -FilePath $chromeExe -ArgumentList $arguments -WindowStyle Normal | Out-Null

for ($attempt = 1; $attempt -le 15; $attempt++) {
    Start-Sleep -Seconds 1
    if (Test-CdpEndpoint $endpoint) {
        Write-Host "Chrome CDP is ready: http://127.0.0.1:$Port"
        exit 0
    }
}

throw "Chrome started but CDP did not become available at http://127.0.0.1:$Port. Check the visible Chrome window for an error."
