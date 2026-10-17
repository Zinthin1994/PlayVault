@echo off
rem Starts the PlayVault UI on Windows (twin of bin/playvault). All the logic is in playvault.ps1.
rem "& exit /b" on the same line: an update may move this folder, so cmd must not read the file again.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0playvault.ps1" %* & exit /b
