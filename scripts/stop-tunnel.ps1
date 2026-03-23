$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "runtime\\cloudflared.pid"
$UrlFile = Join-Path $ProjectRoot "runtime\\cloudflared.url"

if (-not (Test-Path $PidFile)) {
    Write-Output "cloudflared tunnel is not running."
    exit 0
}

$TunnelPid = (Get-Content $PidFile -Raw).Trim()
if (-not $TunnelPid) {
    Remove-Item $PidFile -Force
    Write-Output "Removed empty tunnel PID file."
    exit 0
}

$Process = Get-Process -Id $TunnelPid -ErrorAction SilentlyContinue
if ($Process) {
    Stop-Process -Id $TunnelPid -Force
}

if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
if (Test-Path $UrlFile) { Remove-Item $UrlFile -Force }
Write-Output "Stopped cloudflared tunnel PID $TunnelPid."
