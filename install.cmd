@echo off
rem Double-click to install PlayVault on Windows (runs install.ps1 without changing your execution policy).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
