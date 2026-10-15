@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0package-extension.ps1" %*
exit /b %errorlevel%
