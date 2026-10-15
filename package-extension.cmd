@echo off
setlocal
call "%~dp0scripts\release\package-extension.cmd" %*
exit /b %errorlevel%
