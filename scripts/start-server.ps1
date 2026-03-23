$ErrorActionPreference = "Stop"

$HelperPath = Join-Path $PSScriptRoot "runtime-config.ps1"
. $HelperPath

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$LogsDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $RuntimeDir "uvicorn.pid"
$StdoutLog = Join-Path $LogsDir "uvicorn.stdout.log"
$StderrLog = Join-Path $LogsDir "uvicorn.stderr.log"
$PythonExe = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
$RuntimeConfig = Get-AmadeusRuntimeConfig -ProjectRoot $ProjectRoot
$HostAddress = $RuntimeConfig.Host
$Port = $RuntimeConfig.Port
$QQEnabled = $RuntimeConfig.QQEnabled

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

if (-not (Test-Path $PythonExe)) {
    throw "Project virtual environment Python not found at $PythonExe"
}

if (Test-Path $PidFile) {
    $ExistingServerPid = (Get-Content $PidFile -Raw).Trim()
    if ($ExistingServerPid) {
        $ExistingProcess = Get-Process -Id $ExistingServerPid -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Output "Amadeus server is already running with PID $ExistingServerPid."
            exit 0
        }
    }
    Remove-Item $PidFile -Force
}

$KnownProcess = Find-AmadeusServerProcess -ProjectRoot $ProjectRoot -Port $Port
if ($KnownProcess) {
    Set-Content -Path $PidFile -Value $KnownProcess.ProcessId -Encoding utf8
    Write-Output "Amadeus server is already running with PID $($KnownProcess.ProcessId)."
    exit 0
}

$Arguments = @(
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    $HostAddress,
    "--port",
    $Port
)

$Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

Set-Content -Path $PidFile -Value $Process.Id -Encoding utf8

$Ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $Response = Invoke-RestMethod -Uri "http://${HostAddress}:${Port}/health" -Method Get -TimeoutSec 5
        if ($Response.status -eq "ok") {
            $Ready = $true
            break
        }
    } catch {
    }
}

if (-not $Ready) {
    Write-Output "Amadeus server started with PID $($Process.Id), but health check did not pass in time."
    Write-Output "Configured address: http://${HostAddress}:${Port}"
    Write-Output "Check logs:"
    Write-Output "  $StdoutLog"
    Write-Output "  $StderrLog"
    exit 1
}

$RunningProcess = Find-AmadeusServerProcess -ProjectRoot $ProjectRoot -Port $Port
if ($RunningProcess) {
    Set-Content -Path $PidFile -Value $RunningProcess.ProcessId -Encoding utf8
} else {
    Set-Content -Path $PidFile -Value $Process.Id -Encoding utf8
}

Write-Output "Amadeus server started."
Write-Output "PID: $((Get-Content $PidFile -Raw).Trim())"
Write-Output "Health: http://${HostAddress}:${Port}/health"
Write-Output "Message API: http://${HostAddress}:${Port}/api/messages"
Write-Output "QQ Gateway Mode Enabled: $QQEnabled"
Write-Output "Stdout log: $StdoutLog"
Write-Output "Stderr log: $StderrLog"
