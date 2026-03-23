function Get-AmadeusRuntimeConfig {
    param(
        [string]$ProjectRoot
    )

    $envPath = Join-Path $ProjectRoot ".env"
    $config = @{
        Host = "127.0.0.1"
        Port = "8010"
        QQEnabled = "false"
    }

    if (-not (Test-Path $envPath)) {
        return $config
    }

    foreach ($line in Get-Content $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()

        if ($key -eq "AMADEUS_HOST" -and $value) {
            $config.Host = $value
        }

        if ($key -eq "AMADEUS_PORT" -and $value) {
            $config.Port = $value
        }

        if ($key -eq "AMADEUS_QQ_ENABLED" -and $value) {
            $config.QQEnabled = $value
        }
    }

    return $config
}

function Find-AmadeusServerProcess {
    param(
        [string]$ProjectRoot,
        [string]$Port
    )

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if (-not $listener) {
        return $null
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }

    if ($process.CommandLine -and $process.CommandLine -like "*app.main:app*") {
        return $process
    }

    return $null
}
