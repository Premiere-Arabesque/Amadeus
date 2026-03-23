$ErrorActionPreference = "Stop"

$HelperPath = Join-Path $PSScriptRoot "runtime-config.ps1"
. $HelperPath

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$LogsDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $RuntimeDir "cloudflared.pid"
$UrlFile = Join-Path $RuntimeDir "cloudflared.url"
$LogFile = Join-Path $LogsDir "cloudflared.log"
$CloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$RuntimeConfig = Get-AmadeusRuntimeConfig -ProjectRoot $ProjectRoot
$TargetUrl = "http://{0}:{1}" -f $RuntimeConfig.Host, $RuntimeConfig.Port

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

if (-not (Test-Path $CloudflaredExe)) {
    throw "cloudflared.exe not found at $CloudflaredExe"
}

if (Test-Path $PidFile) {
    $ExistingPid = (Get-Content $PidFile -Raw).Trim()
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Output "cloudflared tunnel is already running with PID $ExistingPid."
            if (Test-Path $UrlFile) {
                Write-Output "Public URL: $((Get-Content $UrlFile -Raw).Trim())"
            }
            exit 0
        }
    }
    Remove-Item $PidFile -Force
}

if (Test-Path $UrlFile) { Remove-Item $UrlFile -Force }
if (Test-Path $LogFile) { Remove-Item $LogFile -Force }

$Arguments = @(
    "tunnel",
    "--url",
    $TargetUrl,
    "--logfile",
    $LogFile,
    "--loglevel",
    "info"
)

$Process = Start-Process `
    -FilePath $CloudflaredExe `
    -ArgumentList $Arguments `
    -WorkingDirectory $ProjectRoot `
    -PassThru

Set-Content -Path $PidFile -Value $Process.Id -Encoding utf8

$PublicUrl = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500

    if (Test-Path $LogFile) {
        $Match = Select-String -Path $LogFile -Pattern 'https://[-a-z0-9]+\.trycloudflare\.com' -AllMatches -ErrorAction SilentlyContinue
        if ($Match) {
            $PublicUrl = $Match.Matches[-1].Value
            break
        }
    }
}

if (-not $PublicUrl) {
    Write-Output "cloudflared started with PID $($Process.Id), but no public URL was detected yet."
    Write-Output "Check logs:"
    Write-Output "  $LogFile"
    exit 1
}

Set-Content -Path $UrlFile -Value $PublicUrl -Encoding utf8

Write-Output "cloudflared tunnel started."
Write-Output "PID: $($Process.Id)"
Write-Output "Target: $TargetUrl"
Write-Output "Public URL: $PublicUrl"
Write-Output "Health URL: $PublicUrl/health"
Write-Output "Message API URL: $PublicUrl/api/messages"
Write-Output "Log file: $LogFile"
