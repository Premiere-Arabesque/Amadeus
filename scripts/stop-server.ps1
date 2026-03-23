$ErrorActionPreference = "Stop"

$HelperPath = Join-Path $PSScriptRoot "runtime-config.ps1"
. $HelperPath

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "runtime\\uvicorn.pid"
$RuntimeConfig = Get-AmadeusRuntimeConfig -ProjectRoot $ProjectRoot
$Port = $RuntimeConfig.Port

if (-not (Test-Path $PidFile)) {
    $DetectedProcess = Find-AmadeusServerProcess -ProjectRoot $ProjectRoot -Port $Port
    if (-not $DetectedProcess) {
        Write-Output "Amadeus server is not running."
        exit 0
    }
    $ServerPid = $DetectedProcess.ProcessId
} else {
    $ServerPid = (Get-Content $PidFile -Raw).Trim()
}

if (-not $ServerPid) {
    Remove-Item $PidFile -Force
    Write-Output "Removed empty PID file."
    exit 0
}

$Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
if (-not $Process) {
    $DetectedProcess = Find-AmadeusServerProcess -ProjectRoot $ProjectRoot -Port $Port
    if (-not $DetectedProcess) {
        if (Test-Path $PidFile) {
            Remove-Item $PidFile -Force
        }
        Write-Output "No running process found for PID $ServerPid. Removed stale PID file."
        exit 0
    }
    $ServerPid = $DetectedProcess.ProcessId
    $Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
}

Stop-Process -Id $ServerPid -Force
if (Test-Path $PidFile) {
    Remove-Item $PidFile -Force
}
Write-Output "Stopped Amadeus server PID $ServerPid."
