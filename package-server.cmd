@echo off
setlocal
call "%~dp0scripts\release\package-server.cmd" %*
exit /b %errorlevel%
