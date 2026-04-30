@echo off
setlocal
cd /d "%~dp0"

set "LAUNCHER="
if exist "%CD%\launcher.py" set "LAUNCHER=%CD%\launcher.py"
if not defined LAUNCHER if exist "%CD%\distribution\launcher.py" set "LAUNCHER=%CD%\distribution\launcher.py"
set "RUNTIME_PY="
if exist "%CD%\runtime\python\python.exe" set "RUNTIME_PY=%CD%\runtime\python\python.exe"
if not defined RUNTIME_PY if exist "%CD%\runtime\python.exe" set "RUNTIME_PY=%CD%\runtime\python.exe"
if not defined RUNTIME_PY if exist "%CD%\..\runtime\python\python.exe" set "RUNTIME_PY=%CD%\..\runtime\python\python.exe"
if not defined RUNTIME_PY if exist "%CD%\..\runtime\python.exe" set "RUNTIME_PY=%CD%\..\runtime\python.exe"

if not defined LAUNCHER (
    echo [CognArch] launcher.py not found.
    pause
    exit /b 1
)

if defined RUNTIME_PY (
    "%RUNTIME_PY%" "%LAUNCHER%" %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3.12 "%LAUNCHER%" %*
    ) else (
        python "%LAUNCHER%" %*
    )
)

if errorlevel 1 pause
