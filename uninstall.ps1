# Removes PlayVault. Your installed games (~\Games\Epic and ~\Games\GOG by default) are NOT deleted.
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1         remove app, keep data (logins, settings)
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -All    also delete data (logins, settings)
param([switch]$All)
$ErrorActionPreference = 'Continue'
$Root = if ($env:PLAYVAULT_HOME) { $env:PLAYVAULT_HOME } else { Join-Path $env:LOCALAPPDATA 'PlayVault' }

if (Get-Process -Name steam -ErrorAction SilentlyContinue) {
    Write-Host 'Note: PlayVault tiles stay in your Steam library until you remove them in Steam.'
}
Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ProcessId -ne $PID -and $_.ExecutablePath.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
foreach ($d in 'app', 'app.prev', 'update', 'venv', 'gogdl', 'electron') {
    Remove-Item -Recurse -Force (Join-Path $Root $d) -ErrorAction SilentlyContinue
}
Remove-Item -Force (Join-Path $Root 'icon.ico') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\PlayVault.lnk') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PlayVault.lnk') -ErrorAction SilentlyContinue
if ($All) {
    Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue
    Write-Host 'PlayVault and all its data removed.'
} else {
    Write-Host "PlayVault removed. Data kept in $Root\data (run with -All to delete it)."
}
Write-Host 'Games were not touched.'
