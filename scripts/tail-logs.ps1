$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogsDir = Join-Path $ProjectRoot "logs"
$StdoutLog = Join-Path $LogsDir "uvicorn.stdout.log"
$StderrLog = Join-Path $LogsDir "uvicorn.stderr.log"
$CloudflaredLog = Join-Path $LogsDir "cloudflared.log"

Write-Output "=== stdout ==="
if (Test-Path $StdoutLog) {
    Get-Content $StdoutLog -Tail 100
} else {
    Write-Output "No stdout log yet."
}

Write-Output ""
Write-Output "=== stderr ==="
if (Test-Path $StderrLog) {
    Get-Content $StderrLog -Tail 100
} else {
    Write-Output "No stderr log yet."
}

Write-Output ""
Write-Output "=== cloudflared ==="
if (Test-Path $CloudflaredLog) {
    Get-Content $CloudflaredLog -Tail 100
} else {
    Write-Output "No cloudflared log yet."
}
