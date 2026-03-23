$ErrorActionPreference = "Stop"

$HelperPath = Join-Path $PSScriptRoot "runtime-config.ps1"
. $HelperPath

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "runtime\\uvicorn.pid"
$RuntimeConfig = Get-AmadeusRuntimeConfig -ProjectRoot $ProjectRoot
$HostAddress = $RuntimeConfig.Host
$Port = $RuntimeConfig.Port

if (-not (Test-Path $PidFile)) {
    $DetectedProcess = Find-AmadeusServerProcess -ProjectRoot $ProjectRoot -Port $Port
    if (-not $DetectedProcess) {
        Write-Output "Amadeus server status: stopped"
        exit 0
    }
    $ServerPid = $DetectedProcess.ProcessId
} else {
    $ServerPid = (Get-Content $PidFile -Raw).Trim()
}

if (-not $ServerPid) {
    Write-Output "Amadeus server status: stopped (empty PID file)"
    exit 0
}

$Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
if (-not $Process) {
    $DetectedProcess = Find-AmadeusServerProcess -ProjectRoot $ProjectRoot -Port $Port
    if (-not $DetectedProcess) {
        Write-Output "Amadeus server status: stopped (stale PID file)"
        exit 0
    }
    $ServerPid = $DetectedProcess.ProcessId
    $Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
}

$Health = "unreachable"
try {
    $Response = Invoke-RestMethod -Uri "http://${HostAddress}:${Port}/health" -Method Get -TimeoutSec 5
    $Health = $Response.status
} catch {
}

Write-Output "Amadeus server status: running"
Write-Output "PID: $ServerPid"
Write-Output "Health: $Health"
Write-Output "Base URL: http://${HostAddress}:${Port}"
