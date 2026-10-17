@echo off
rem Launches one game (Epic or GOG):  playvault-launch <app_name> [game args]
rem Steam library tiles created by PlayVault point here (twin of bin/playvault-launch).
setlocal
for %%I in ("%~dp0..") do set "APP=%%~fI"
if not defined PLAYVAULT_HOME for %%I in ("%APP%\..") do set "PLAYVAULT_HOME=%%~fI"
set "PYTHONPATH=%APP%"
"%PLAYVAULT_HOME%\venv\Scripts\python.exe" -m evault.launcher %*
exit /b %errorlevel%
