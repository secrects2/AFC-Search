$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---------------------------------------------------------------
# AFC Price Monitor — Cloudflare Tunnel 設定
# 讓公司同事透過網際網路存取你的本機 Dashboard
# ---------------------------------------------------------------

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Cloudflare Tunnel 設定" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check if cloudflared is installed
$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue

if (-not $cloudflared) {
    Write-Host "[1/4] 安裝 cloudflared..." -ForegroundColor Yellow
    
    # Try winget first
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "  使用 winget 安裝..."
        winget install Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements
    } else {
        # Direct download
        $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.msi"
        $msiPath = Join-Path $env:TEMP "cloudflared.msi"
        Write-Host "  下載 cloudflared..."
        Invoke-WebRequest -Uri $url -OutFile $msiPath -UseBasicParsing
        Write-Host "  安裝中..."
        Start-Process msiexec.exe -ArgumentList "/i", $msiPath, "/quiet", "/norestart" -Wait
        Remove-Item $msiPath -Force -ErrorAction SilentlyContinue
    }
    
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    
    $cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    if (-not $cloudflared) {
        Write-Host ""
        Write-Host "  cloudflared 安裝可能需要重開終端機。" -ForegroundColor Yellow
        Write-Host "  請重開 PowerShell 後再次執行此腳本。" -ForegroundColor Yellow
        exit 0
    }
    Write-Host "  OK — cloudflared 已安裝" -ForegroundColor Green
} else {
    Write-Host "[1/4] cloudflared 已安裝 OK" -ForegroundColor Green
}

# Step 2: Login to Cloudflare
Write-Host ""
Write-Host "[2/4] 登入 Cloudflare..." -ForegroundColor Yellow
Write-Host "  這會開啟瀏覽器，請選擇你要使用的域名(domain)。" -ForegroundColor Gray
Write-Host ""

cloudflared tunnel login

if ($LASTEXITCODE -ne 0) {
    Write-Host "登入失敗，請重試。" -ForegroundColor Red
    exit 1
}
Write-Host "  OK — 已登入 Cloudflare" -ForegroundColor Green

# Step 3: Create tunnel
Write-Host ""
Write-Host "[3/4] 建立 Tunnel..." -ForegroundColor Yellow

$tunnelName = "afc-monitor"

# Check if tunnel already exists
$existingTunnels = cloudflared tunnel list 2>&1 | Select-String $tunnelName
if ($existingTunnels) {
    Write-Host "  Tunnel '$tunnelName' 已存在，跳過建立。" -ForegroundColor Gray
} else {
    cloudflared tunnel create $tunnelName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "建立 Tunnel 失敗。" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  OK — Tunnel '$tunnelName' 就緒" -ForegroundColor Green

# Step 4: Get the tunnel ID and prompt for domain
$tunnelInfo = cloudflared tunnel list 2>&1 | Select-String $tunnelName
$tunnelId = ($tunnelInfo -split '\s+')[0]

Write-Host ""
Write-Host "[4/4] 設定域名..." -ForegroundColor Yellow
Write-Host ""
$domain = Read-Host "  請輸入你的子域名（例如 afc.yourdomain.com）"

if (-not $domain) {
    Write-Host "  未輸入域名，跳過 DNS 設定。" -ForegroundColor Yellow
    Write-Host "  你可以之後手動執行：cloudflared tunnel route dns $tunnelName YOUR_DOMAIN" -ForegroundColor Gray
} else {
    cloudflared tunnel route dns $tunnelName $domain
    Write-Host "  OK — 已設定 DNS：$domain" -ForegroundColor Green
}

# Create config file
$cfConfigDir = Join-Path $env:USERPROFILE ".cloudflared"
if (-not (Test-Path $cfConfigDir)) {
    New-Item -ItemType Directory -Path $cfConfigDir -Force | Out-Null
}

$configContent = @"
tunnel: $tunnelId
credentials-file: $cfConfigDir\$tunnelId.json

ingress:
  - hostname: $domain
    service: http://127.0.0.1:8001
  - service: http_status:404
"@

$configPath = Join-Path $cfConfigDir "config.yml"
$configContent | Out-File -FilePath $configPath -Encoding UTF8
Write-Host ""
Write-Host "  設定檔已寫入：$configPath" -ForegroundColor Gray

# Create a bat file to run the tunnel
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tunnelBat = Join-Path $ProjectRoot "run_tunnel.bat"
@"
@echo off
REM AFC Cloudflare Tunnel
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo [%date% %time%] Tunnel 啟動 >> logs\tunnel.log
cloudflared tunnel run $tunnelName >> logs\tunnel.log 2>&1
"@ | Out-File -FilePath $tunnelBat -Encoding ASCII

# Register as Windows scheduled task (auto-start on login)
$tunnelAction = New-ScheduledTaskAction `
    -Execute $tunnelBat `
    -WorkingDirectory $ProjectRoot

$tunnelTrigger = New-ScheduledTaskTrigger -AtLogOn

$tunnelSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel LeastPrivilege

Register-ScheduledTask `
    -TaskName "AFC Price Monitor - Cloudflare Tunnel" `
    -Action $tunnelAction `
    -Trigger $tunnelTrigger `
    -Settings $tunnelSettings `
    -Principal $principal `
    -Description "AFC Cloudflare Tunnel — 讓同事連線到 Dashboard" `
    -Force | Out-Null

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Cloudflare Tunnel 設定完成！" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""
if ($domain) {
    Write-Host "  你的同事可以透過以下網址存取 Dashboard：" -ForegroundColor White
    Write-Host "  https://$domain" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "  Tunnel 已設定為開機自動啟動。" -ForegroundColor White
Write-Host "  手動啟動：cloudflared tunnel run $tunnelName" -ForegroundColor Gray
Write-Host ""
Write-Host "  建議下一步：到 Cloudflare Zero Trust 設定 Access Policy" -ForegroundColor Yellow
Write-Host "  限制只有公司 email 才能登入（免費功能）" -ForegroundColor Yellow
Write-Host ""
