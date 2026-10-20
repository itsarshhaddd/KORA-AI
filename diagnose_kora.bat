@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo KORA V13 - FULL SYSTEM / API DIAGNOSTIC V5
echo ================================================================
if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)
echo [INFO] Using: %PYTHON%
echo.
%PYTHON% diagnose_kora.py
set "RC=%ERRORLEVEL%"
echo.
echo ================================================================
echo Diagnostic exit code: %RC%
echo ================================================================
pause
exit /b %RC%
