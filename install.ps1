# PlayVault installer for Windows 10/11 (twin of install.sh).
# Run from the extracted PlayVault folder:   powershell -ExecutionPolicy Bypass -File install.ps1
# (or double-click install.cmd). Re-running it updates PlayVault and keeps your settings, logins and games.
#
# Options:  -NoSteam   don't add the PlayVault tile to Steam
param([switch]$NoSteam)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is far faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ElectronVersion = '31.7.7'
$GogdlFallbackVersion = 'v1.3.0'
$LegendaryUrl = 'https://github.com/derrod/legendary/archive/refs/heads/master.zip'

$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = if ($env:PLAYVAULT_HOME) { $env:PLAYVAULT_HOME } else { Join-Path $env:LOCALAPPDATA 'PlayVault' }
$Arm = $env:PROCESSOR_ARCHITECTURE -eq 'ARM64'

function Bold($msg) { Write-Host ''; Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!! $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "xx $msg" -ForegroundColor Red; exit 1 }

if ($env:OS -ne 'Windows_NT') { Die 'This installer is for Windows. On the Steam Machine / Steam Deck run install.sh.' }

# ------------------------------------------------------------------ python
function Find-Python {
    $candidates = @(
        @{ Exe = 'py'; Args = @('-3') },
        @{ Exe = 'python'; Args = @() },
        @{ Exe = 'python3'; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $ver = & $c.Exe @($c.Args) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        # the Microsoft Store "python" stub prints nothing useful and exits non-zero
        if ($LASTEXITCODE -eq 0 -and "$ver" -match '^(\d+)\.(\d+)$' -and ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9))) {
            return $c
        }
    }
    return $null
}
$Py = Find-Python
if (-not $Py) { Die 'Python 3.9 or newer is required. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run this again.' }

# ------------------------------------------------------------------ stop a running PlayVault
Bold "Installing PlayVault into $Root"
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\logs') | Out-Null
Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ProcessId -ne $PID -and $_.ExecutablePath.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# ------------------------------------------------------------------ app files
Bold 'Copying app files'
$new = Join-Path $Root 'app.new'
Remove-Item -Recurse -Force $new -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force (Join-Path $Src 'app') $new
Get-ChildItem $new -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force (Join-Path $Root 'app') -ErrorAction SilentlyContinue
Move-Item $new (Join-Path $Root 'app')
# a manual install replaces any self-update that was downloaded or half-installed
Remove-Item -Recurse -Force (Join-Path $Root 'update'), (Join-Path $Root 'app.prev') -ErrorAction SilentlyContinue

# ------------------------------------------------------------------ python + legendary
Bold 'Setting up Python environment with legendary (Epic client)'
$VenvPy = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    & $Py.Exe @($Py.Args) -m venv (Join-Path $Root 'venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Die 'Could not create a Python venv' }
}
& $VenvPy -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Warn 'pip self-upgrade failed (continuing)' }
& $VenvPy -m pip install --quiet --upgrade $LegendaryUrl
if ($LASTEXITCODE -ne 0) {
    Warn 'Installing legendary from GitHub failed, trying PyPI'
    & $VenvPy -m pip install --quiet --upgrade legendary-gl
    if ($LASTEXITCODE -ne 0) { Die 'Could not install legendary' }
}
& (Join-Path $Root 'venv\Scripts\legendary.exe') --version
if ($LASTEXITCODE -ne 0) { Die 'legendary did not install correctly' }

# ------------------------------------------------------------------ gogdl (GOG client)
Bold 'Installing gogdl (GOG client from Heroic)'
$gogdlAsset = if ($Arm) { 'gogdl_windows_arm64.exe' } else { 'gogdl_windows_x86_64.exe' }
$gogdlUrl = $null
try {
    $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/Heroic-Games-Launcher/heroic-gogdl/releases/latest' -Headers @{ 'User-Agent' = 'PlayVault-installer' }
    $gogdlUrl = ($rel.assets | Where-Object { $_.name -eq $gogdlAsset } | Select-Object -First 1).browser_download_url
} catch { }
if (-not $gogdlUrl) { $gogdlUrl = "https://github.com/Heroic-Games-Launcher/heroic-gogdl/releases/download/$GogdlFallbackVersion/$gogdlAsset" }
Write-Host "    $gogdlUrl"
$gogdlDir = Join-Path $Root 'gogdl'
New-Item -ItemType Directory -Force -Path $gogdlDir | Out-Null
try {
    $tmpGogdl = Join-Path $env:TEMP 'playvault-gogdl.exe'
    Invoke-WebRequest -Uri $gogdlUrl -OutFile $tmpGogdl -UseBasicParsing
    Move-Item -Force $tmpGogdl (Join-Path $gogdlDir 'gogdl.exe')
    & (Join-Path $gogdlDir 'gogdl.exe') --version
    if ($LASTEXITCODE -ne 0) { Warn 'gogdl did not start - GOG support will be unavailable' }
} catch {
    Warn 'Downloading gogdl failed - GOG support will be unavailable until you re-run the installer'
}

# ------------------------------------------------------------------ electron
$versionFile = Join-Path $Root 'electron\.version'
$have = if (Test-Path $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { '' }
if ($have -ne $ElectronVersion -or -not (Test-Path (Join-Path $Root 'electron\electron.exe'))) {
    Bold "Downloading Electron $ElectronVersion (UI runtime, ~100 MB)"
    $arch = if ($Arm) { 'arm64' } else { 'x64' }
    $zip = Join-Path $env:TEMP 'playvault-electron.zip'
    Invoke-WebRequest -Uri "https://github.com/electron/electron/releases/download/v$ElectronVersion/electron-v$ElectronVersion-win32-$arch.zip" -OutFile $zip -UseBasicParsing
    Remove-Item -Recurse -Force (Join-Path $Root 'electron') -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Root 'electron') -Force
    Remove-Item -Force $zip
    Set-Content -Path $versionFile -Value $ElectronVersion
} else {
    Bold "Electron $ElectronVersion already installed"
}

# ------------------------------------------------------------------ Start menu entry
Bold 'Creating Start menu entry'
# an .ico wrapping the app's 256px PNG (Windows accepts PNG-compressed icons)
function New-IcoFromPng($png, $ico) {
    $b = [IO.File]::ReadAllBytes($png)
    $w = ($b[16] -shl 24) -bor ($b[17] -shl 16) -bor ($b[18] -shl 8) -bor $b[19]
    $h = ($b[20] -shl 24) -bor ($b[21] -shl 16) -bor ($b[22] -shl 8) -bor $b[23]
    $ms = New-Object IO.MemoryStream
    $bw = New-Object IO.BinaryWriter($ms)
    $bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]1)                 # ICONDIR: one image
    $bw.Write([byte]($w % 256)); $bw.Write([byte]($h % 256)); $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([uint16]1); $bw.Write([uint16]32); $bw.Write([uint32]$b.Length); $bw.Write([uint32]22)
    $bw.Write($b)
    [IO.File]::WriteAllBytes($ico, $ms.ToArray())
}
$icoPath = Join-Path $Root 'icon.ico'
try { New-IcoFromPng (Join-Path $Root 'app\art\icon.png') $icoPath } catch { $icoPath = $null }
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $startMenu 'PlayVault.lnk'))
$lnk.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $Root 'app\bin\playvault.ps1')`""
$lnk.WorkingDirectory = $Root
$lnk.Description = 'Epic Games + GOG library'
if ($icoPath) { $lnk.IconLocation = $icoPath }
$lnk.Save()

# ------------------------------------------------------------------ Steam tile
function Steam-Py($what) {
    $env:PLAYVAULT_HOME = $Root
    $env:PYTHONPATH = Join-Path $Root 'app'
    & $VenvPy -m evault.steam $what
}
$steamProc = Get-Process -Name steam -ErrorAction SilentlyContinue
if ($NoSteam) {
    Write-Host 'Skipping the Steam tile (-NoSteam).'
} else {
    Bold 'Adding PlayVault to your Steam library'
    $steamExe = $null
    try { $steamExe = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction Stop).SteamExe } catch { }
    $restartSteam = $false
    if ($steamProc) {
        Write-Host 'Steam has to close for a moment so the library tiles stick.'
        $ans = Read-Host 'Close Steam now? [Y/n]'
        if (-not $ans -or $ans -match '^[Yy]') {
            if ($steamExe) { Start-Process -FilePath $steamExe -ArgumentList '-shutdown' }
            for ($i = 0; $i -lt 60 -and (Get-Process -Name steam -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }
            $restartSteam = $true
        }
    }
    # with Steam closed this writes the tiles now; otherwise it stages them and PlayVault
    # shows a "Restart Steam" banner
    try { Steam-Py 'add-self' } catch { Warn 'Could not add the Steam tile (log into Steam once, then use Settings > Re-add PlayVault to Steam)' }
    if ($restartSteam -and $steamExe) { Start-Process -FilePath $steamExe }
}

Bold 'Done!'
Write-Host @"

  PlayVault is installed.

  * Start it:      Start menu > PlayVault (or from your Steam library, Non-Steam)
  * First start:   sign in with your Epic and/or GOG account.

  Logs: $Root\data\logs
  Update later by running this installer again (or from Settings > PlayVault updates).
"@
