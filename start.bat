@echo off
setlocal
cd /d "%~dp0"
call "%~dp0distribution\start.bat" %*
exit /b %errorlevel%
