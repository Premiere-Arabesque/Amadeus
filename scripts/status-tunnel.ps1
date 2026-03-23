$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "runtime\\cloudflared.pid"
$UrlFile = Join-Path $ProjectRoot "runtime\\cloudflared.url"

if (-not (Test-Path $PidFile)) {
    Write-Output "cloudflared status: stopped"
    exit 0
}

$TunnelPid = (Get-Content $PidFile -Raw).Trim()
if (-not $TunnelPid) {
    Write-Output "cloudflared status: stopped (empty PID file)"
    exit 0
}

$Process = Get-Process -Id $TunnelPid -ErrorAction SilentlyContinue
if (-not $Process) {
    Write-Output "cloudflared status: stopped (stale PID file)"
    exit 0
}

Write-Output "cloudflared status: running"
Write-Output "PID: $TunnelPid"
if (Test-Path $UrlFile) {
    Write-Output "Public URL: $((Get-Content $UrlFile -Raw).Trim())"
}
