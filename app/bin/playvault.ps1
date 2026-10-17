# Starts the PlayVault UI on Windows (twin of bin/playvault).
# Before starting it installs a PlayVault update the backend has downloaded and verified
# (ROOT\update, see evault/updater.py), and rolls back to the previous version if an
# update fails to start twice.
param([Parameter(ValueFromRemainingArguments = $true)] $Rest)
$ErrorActionPreference = 'Continue'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$App = Split-Path -Parent $Here
$Root = if ($env:PLAYVAULT_HOME) { $env:PLAYVAULT_HOME } else { Split-Path -Parent $App }
$Port = if ($env:PLAYVAULT_PORT) { $env:PLAYVAULT_PORT } else { '47821' }
$U = Join-Path $Root 'update'
$Prev = Join-Path $Root 'app.prev'
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\logs') | Out-Null

function Ulog($msg) {
    Add-Content -Path (Join-Path $Root 'data\logs\update.log') -Value ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}

function Api($method, $path, $timeout = 5) {
    try { Invoke-RestMethod -Method $method -Uri "http://127.0.0.1:$Port$path" -TimeoutSec $timeout } catch { $null }
}

# Stops the backend so its files can be replaced. Fails if it's busy (download or game).
function Stop-Backend {
    $r = Api 'POST' '/api/update/prepare'
    if ($r -and -not $r.ok) { Ulog ('not now: ' + ($r | ConvertTo-Json -Compress)); return $false }
    if (Api 'GET' '/api/ping' 1) { Api 'POST' '/api/quit' | Out-Null }
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Api 'GET' '/api/ping' 1)) { Start-Sleep -Milliseconds 600; return $true }
        Start-Sleep -Milliseconds 250
    }
    return $true
}

# Windows holds a folder while anything is still closing, so retry a few times.
function Move-Dir($from, $to) {
    for ($i = 0; $i -lt 12; $i++) {
        try { Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop; return $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

function Restart-Self {
    & (Join-Path $App 'bin\playvault.ps1') @Rest
    exit
}

# ---------------------------------------------------------------- rollback
# trial.json exists until the new version's UI reports in (POST /api/update/confirm).
if ((Test-Path "$U\trial.json") -and (Test-Path $Prev)) {
    $tries = 1
    if (Test-Path "$U\trial.tries") { $tries = ([int]((Get-Content "$U\trial.tries" -Raw).Trim())) + 1 }
    if ($tries -gt 2) {
        if (Stop-Backend) {
            Ulog ('update ' + (Get-Content "$U\trial.json" -Raw).Trim() + ' did not start twice - rolling back')
            Remove-Item -Recurse -Force "$U\failed-app" -ErrorAction SilentlyContinue
            if ((Move-Dir $App "$U\failed-app") -and (Move-Dir $Prev $App)) {
                Move-Item -Force "$U\trial.json" "$U\failed.json"
                Remove-Item -Force "$U\trial.tries" -ErrorAction SilentlyContinue
                Restart-Self
            }
            Ulog 'rollback failed'
        }
    } else {
        Set-Content -Path "$U\trial.tries" -Value $tries
    }
}

# ---------------------------------------------------------------- install a staged update
if ((Test-Path "$U\ready.json") -and (Test-Path "$U\app\bin\playvault.ps1") -and -not (Test-Path "$U\trial.json")) {
    if (Stop-Backend) {
        Remove-Item -Recurse -Force $Prev -ErrorAction SilentlyContinue
        if (Move-Dir $App $Prev) {
            if (Move-Dir "$U\app" $App) {
                Move-Item -Force "$U\ready.json" "$U\trial.json"
                Remove-Item -Force "$U\trial.tries", "$U\failed.json" -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force "$U\failed-app" -ErrorAction SilentlyContinue
                Ulog ('installed update ' + (Get-Content "$U\trial.json" -Raw).Trim())
                Restart-Self
            }
            Move-Dir $Prev $App | Out-Null
        }
        Ulog 'could not install the update (file move failed)'
    }
}

# ---------------------------------------------------------------- start the UI
$env:PLAYVAULT_ROOT = $Root
$env:PLAYVAULT_APP = $App
$env:PLAYVAULT_HOME = $Root
$logs = Join-Path $Root 'data\logs'
$argList = @("`"$(Join-Path $App 'electron')`"") + @($Rest | Where-Object { $_ })
# WaitForExit on the process itself (Start-Process -Wait would also wait for the background backend)
$p = Start-Process -FilePath (Join-Path $Root 'electron\electron.exe') -ArgumentList $argList -WorkingDirectory $Root `
    -RedirectStandardOutput (Join-Path $logs 'electron.log') -RedirectStandardError (Join-Path $logs 'electron.err.log') -PassThru
$p.WaitForExit()
